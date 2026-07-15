from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from zerohandoff.config import digest_file, digest_value
from zerohandoff.models import GateDecision, GateResult, Stage
from zerohandoff.storage import RunStore


@dataclass(frozen=True)
class BundleResult:
    bundle_dir: Path
    manifest_path: Path
    checksums_path: Path
    checksums: dict[str, str]


class DeliveryBundleAssembler:
    def assemble(self, store: RunStore, *, record_event: bool = True) -> BundleResult:
        sources: list[tuple[Path, str]] = []
        for source, target in (
            (store.workspace_dir / "app", "app"),
            (store.artifacts_dir, "evidence/artifacts"),
            (store.logs_dir, "evidence/logs"),
            (store.root / "events.jsonl", "evidence/events.jsonl"),
            (store.root / "manifest.json", "evidence/run-manifest.json"),
            (store.root / "build_request.json", "evidence/build_request.json"),
            (store.root / "settings.snapshot.json", "evidence/settings.snapshot.json"),
            (store.root / "frozen_relationship_vectors.json", "evidence/frozen_relationship_vectors.json"),
            (store.root / "demo", "demo"),
        ):
            if source.exists():
                sources.append((source, target))
        store.copy_into_bundle(sources)
        setup = store.bundle_dir / "SETUP.md"
        setup.write_text(
            "# Run the delivered application\n\n"
            "```bash\ncd app\nnpm install\nnpm test\nnpm run build\nnpm run dev\n```\n\n"
            "The standalone generated preview is also available at `app/dist/index.html`.\n"
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
            "launch": "app/dist/index.html",
            "video": "demo/demo.mp4",
            "evidence": "evidence/",
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
        if not verified or manifest["checksums_digest"] != digest_value(checksums):
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
                        "bundle.setup_present": setup.is_file(),
                    },
                    evidence=["delivery_bundle/delivery_manifest.json", "delivery_bundle/checksums.json"],
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
