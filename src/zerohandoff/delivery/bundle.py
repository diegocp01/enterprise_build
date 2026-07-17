from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from zerohandoff.config import digest_file, digest_value
from zerohandoff.models import GateDecision, GateResult, Stage
from zerohandoff.storage import RunStore

try:  # pragma: no cover - platform import
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - platform import
    import msvcrt
except ImportError:  # pragma: no cover - POSIX path
    msvcrt = None  # type: ignore[assignment]


@dataclass(frozen=True)
class BundleResult:
    bundle_dir: Path
    manifest_path: Path
    checksums_path: Path
    checksums: dict[str, str]


@contextmanager
def _bundle_lock(path: Path):
    """Serialize bundle replacement across processes, not just RunStore instances."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows fallback
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _portable_preview(bundle_dir: Path) -> bool:
    index = bundle_dir / "app" / "dist" / "index.html"
    if not index.is_file():
        return False
    dist = index.parent.resolve()
    for reference in re.findall(
        r'(?:src|href)=["\']([^"\']+)["\']', index.read_text(errors="ignore")
    ):
        clean = reference.split("?", 1)[0].split("#", 1)[0]
        if not clean or clean.startswith(("#", "data:", "mailto:")):
            continue
        if clean.startswith(("/", "http://", "https://", "//")):
            return False
        target = (dist / clean).resolve()
        if dist not in target.parents or not target.is_file():
            return False
    return not any(
        re.search(r" \d+\.[^.]+$", path.name)
        for path in (dist / "assets").glob("*")
        if path.is_file()
    )


def _physical_files(bundle_dir: Path) -> set[str]:
    return {
        str(path.relative_to(bundle_dir))
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }


def _stabilize_checksum_scope(
    bundle_dir: Path, expected_files: set[str], *, timeout_seconds: float = 12.0
) -> bool:
    """Remove generated conflict ghosts and require a clean quiescent window."""

    deadline = time.monotonic() + timeout_seconds
    clean_observations = 0
    while time.monotonic() < deadline:
        physical = _physical_files(bundle_dir)
        extras = physical - expected_files
        for relative in sorted(extras):
            candidate = (bundle_dir / relative).resolve()
            if bundle_dir.resolve() in candidate.parents and candidate.is_file():
                candidate.unlink(missing_ok=True)
        physical = _physical_files(bundle_dir)
        if physical == expected_files:
            clean_observations += 1
            if clean_observations >= 6:
                return True
        else:
            clean_observations = 0
        time.sleep(0.5)
    return False


class DeliveryBundleAssembler:
    def assemble(self, store: RunStore, *, record_event: bool = True) -> BundleResult:
        with _bundle_lock(store.root / ".delivery_bundle.lock"):
            return self._assemble_locked(store, record_event=record_event)

    def _assemble_locked(
        self, store: RunStore, *, record_event: bool
    ) -> BundleResult:
        sources: list[tuple[Path, str]] = []
        for source, target in (
            (store.workspace_dir / "app", "app"),
            (store.logs_dir, "evidence/logs"),
            (store.root / "events.jsonl", "evidence/events.jsonl"),
            (store.root / "manifest.json", "evidence/run-manifest.json"),
            (store.root / "build_request.json", "evidence/build_request.json"),
            (store.root / "settings.snapshot.json", "evidence/settings.snapshot.json"),
            (store.root / "frozen_relationship_vectors.json", "evidence/frozen_relationship_vectors.json"),
            (store.root / "inference_relationships.start.json", "evidence/inference_relationships.start.json"),
            (store.root / "inference_relationships.end.json", "evidence/inference_relationships.end.json"),
            (store.root / "inference_relationship_deltas.json", "evidence/inference_relationship_deltas.json"),
            (store.root / "inference_night_commit.json", "evidence/inference_night_commit.json"),
        ):
            if source.exists():
                sources.append((source, target))
        # Artifact evidence is allowlisted from the append-only artifact log.
        # This prevents filesystem conflict copies or undeclared scratch files
        # from silently entering an otherwise checksummed delivery bundle.
        declared_artifacts: dict[str, Path] = {}
        for row in RunStore.read_jsonl(store.logs_dir / "artifacts.jsonl"):
            for relative in row.get("content_files", []):
                source = store.root / str(relative)
                if source.is_file():
                    declared_artifacts[str(relative)] = source
                    envelope = source.parent / "envelope.json"
                    if envelope.is_file():
                        envelope_relative = str(envelope.relative_to(store.root))
                        declared_artifacts[envelope_relative] = envelope
        for relative, source in sorted(declared_artifacts.items()):
            artifact_relative = Path(relative).relative_to("artifacts")
            sources.append((source, f"evidence/artifacts/{artifact_relative}"))
        # Ship the polished demo and human-readable evidence, not Playwright's
        # raw WebM capture, hidden recorder cache, or superseded voice files.
        for filename in (
            "demo.mp4",
            "app-preview.png",
            "demo_plan.json",
            "narration.txt",
            "narration.mp3",
            "capture_report.json",
        ):
            source = store.root / "demo" / filename
            if source.is_file():
                sources.append((source, f"demo/{filename}"))
        store.copy_into_bundle(sources)
        setup = store.bundle_dir / "SETUP.md"
        setup.write_text(
            "# Run the delivered application\n\n"
            "```bash\ncd app\nnpm ci --ignore-scripts\nnpm test\nnpm run build\nnpm run dev -- --host 127.0.0.1\n```\n\n"
            "Then open `http://127.0.0.1:5173/`. Browser security rules can block "
            "JavaScript modules opened directly with `file://`, so serve the app over HTTP.\n"
        )
        checksums: dict[str, str] = {}
        for path in sorted(store.bundle_dir.rglob("*")):
            if path.is_file() and path.name not in {"checksums.json", "delivery_manifest.json"}:
                checksums[str(path.relative_to(store.bundle_dir))] = digest_file(path)
        manifest = {
            "schema_version": "1.0",
            "run_id": store.run_id,
            "contents": sorted(checksums),
            "checksums_digest": digest_value(checksums),
            "launch": {
                "command": "cd app && npm run dev -- --host 127.0.0.1",
                "url": "http://127.0.0.1:5173/",
            },
            "video": "demo/demo.mp4",
            "evidence": "evidence/",
            "checksums_scope": (
                "Every bundled file except checksums.json and delivery_manifest.json"
            ),
        }
        manifest_path = store.bundle_dir / "delivery_manifest.json"
        checksums_path = store.bundle_dir / "checksums.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        checksums_path.write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n")
        verified = all(
            (store.bundle_dir / relative).is_file()
            and digest_file(store.bundle_dir / relative) == expected
            for relative, expected in checksums.items()
        )
        expected_files = set(checksums) | {
            "checksums.json",
            "delivery_manifest.json",
        }
        checksums_scope_complete = _stabilize_checksum_scope(
            store.bundle_dir, expected_files
        )
        portable_preview = _portable_preview(store.bundle_dir)
        if (
            not verified
            or not checksums_scope_complete
            or manifest["checksums_digest"] != digest_value(checksums)
            or not portable_preview
        ):
            raise RuntimeError("delivery bundle checksum validation failed")
        if record_event:
            store.append_log(
                "gates",
                GateResult(
                    stage=Stage.BUNDLE,
                    decision=GateDecision.PASS,
                    rule_results={
                        "bundle.manifest_complete": bool(manifest["contents"]),
                        "bundle.checksums_valid": verified,
                        "bundle.checksums_scope_complete": checksums_scope_complete,
                        "bundle.setup_present": setup.is_file(),
                        "bundle.preview_portable": portable_preview,
                    },
                    evidence=[
                        f"{store.bundle_dir.name}/delivery_manifest.json",
                        f"{store.bundle_dir.name}/checksums.json",
                    ],
                ).model_dump(mode="json"),
            )
            store.append_event(
                event_type="delivery.bundle.completed",
                status="completed",
                stage="BUNDLE",
                output_refs=[str(store.bundle_dir.relative_to(store.root))],
                payload={"file_count": len(checksums)},
            )
        return BundleResult(store.bundle_dir, manifest_path, checksums_path, checksums)
