from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable

from zerohandoff.config import canonical_json, digest_file, digest_value
from zerohandoff.models import (
    ArtifactEnvelope,
    EventRecord,
    GateDecision,
    RunManifest,
    Stage,
    utc_now,
)


SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class StoreError(RuntimeError):
    pass


class RunStore:
    """Single-writer run store with append-only events and immutable artifacts."""

    LOG_CATEGORIES = {
        "agent_calls",
        "artifacts",
        "gates",
        "repairs",
        "commands",
        "demo",
        "episodes",
        "night_updates",
        "night_workspace",
        "relationship_vectors",
        "relationship_policies",
        "memory_decisions",
        "memories_state",
        "forgotten",
        "puzzle_validation",
        "run_manifests",
        "handoff_rewards",
        "shadow_trust_updates",
        "inference_night_commits",
        "inference_memory",
    }

    def __init__(self, base_dir: Path, run_id: str, phase: str) -> None:
        if not SAFE_ID.match(run_id):
            raise StoreError("run_id contains unsafe characters")
        if phase not in {"training", "delivery"}:
            raise StoreError("phase must be training or delivery")
        self.base_dir = base_dir.resolve()
        self.run_id = run_id
        self.phase = phase
        self.root = (self.base_dir / run_id).resolve()
        if self.base_dir not in self.root.parents:
            raise StoreError("run path escapes base directory")
        self.logs_dir = self.root / "logs"
        self.artifacts_dir = self.root / "artifacts"
        self.raw_dir = self.root / "raw"
        self.workspace_dir = self.root / "workspace"
        self.bundle_dir = self.root / "delivery_bundle"
        for directory in (
            self.root,
            self.logs_dir,
            self.artifacts_dir,
            self.raw_dir,
            self.workspace_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sequence = self._discover_last_sequence()

    def _discover_last_sequence(self) -> int:
        events_path = self.root / "events.jsonl"
        if not events_path.exists():
            return 0
        last = 0
        for row in self.read_jsonl(events_path):
            last = max(last, int(row.get("sequence", 0)))
        return last

    @staticmethod
    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise StoreError(f"invalid JSONL at {path}:{line_number}") from exc
        return rows

    def atomic_json(self, relative_path: str | Path, value: Any) -> Path:
        path = self._safe_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
        return path

    def atomic_jsonl(
        self,
        relative_path: str | Path,
        values: Iterable[Any],
    ) -> Path:
        path = self._safe_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            for value in values
        ]
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text("".join(f"{canonical_json(row)}\n" for row in rows))
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
        return path

    def _safe_path(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise StoreError("run-store paths must be relative")
        path = (self.root / relative).resolve()
        if path != self.root and self.root not in path.parents:
            raise StoreError("path escapes run root")
        return path

    def append_jsonl(self, relative_path: str | Path, value: Any) -> Path:
        path = self._safe_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        encoded = canonical_json(payload) + "\n"
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        return path

    def append_event(
        self,
        *,
        event_type: str,
        status: str,
        stage: str | None = None,
        actor: str | None = None,
        adapter: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        relationship_vector_digest: str | None = None,
        settings_digest: str | None = None,
        attempt: int | None = None,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        duration_ms: int | None = None,
        usage: dict[str, Any] | None = None,
        git_commit: str | None = None,
        error: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        with self._lock:
            self._sequence += 1
            event = EventRecord(
                event_id=f"evt_{uuid.uuid4().hex}",
                sequence=self._sequence,
                run_id=self.run_id,
                event_type=event_type,
                phase=self.phase,  # type: ignore[arg-type]
                stage=stage,
                actor=actor,
                adapter=adapter,
                model=model,
                reasoning_effort=reasoning_effort,
                relationship_vector_digest=relationship_vector_digest,
                settings_digest=settings_digest,
                attempt=attempt,
                input_refs=input_refs or [],
                output_refs=output_refs or [],
                status=status,
                duration_ms=duration_ms,
                usage=usage,
                git_commit=git_commit,
                error=error,
                payload=payload or {},
            )
            path = self.root / "events.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(event.model_dump(mode="json")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event

    def append_log(self, category: str, value: dict[str, Any]) -> Path:
        if category not in self.LOG_CATEGORIES:
            raise StoreError(f"unknown log category: {category}")
        if not value.get("timestamp"):
            value = {**value, "timestamp": utc_now().isoformat()}
        row = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "phase": self.phase,
            **value,
        }
        return self.append_jsonl(Path("logs") / f"{category}.jsonl", row)

    def write_manifest(self, manifest: RunManifest) -> Path:
        if manifest.run_id != self.run_id:
            raise StoreError("manifest run_id does not match store")
        path = self.atomic_json("manifest.json", manifest)
        self.append_log("run_manifests", manifest.model_dump(mode="json"))
        return path

    def write_state(self, value: dict[str, Any]) -> Path:
        return self.atomic_json("state.json", value)

    def read_state(self) -> dict[str, Any] | None:
        path = self.root / "state.json"
        return json.loads(path.read_text()) if path.exists() else None

    def commit_artifact(
        self,
        *,
        stage: Stage,
        artifact_id: str,
        artifact_type: str,
        version: int,
        producer_pair: str,
        lead: str,
        peer: str,
        files: dict[str, str | dict[str, Any] | list[Any]],
        input_digests: dict[str, str] | None = None,
        contract_item_ids: list[str] | None = None,
        gate_status: GateDecision = GateDecision.PASS,
    ) -> ArtifactEnvelope:
        if not SAFE_ID.match(artifact_id):
            raise StoreError("artifact_id contains unsafe characters")
        directory = self.artifacts_dir / stage.value.lower() / artifact_id / str(version)
        if directory.exists():
            raise StoreError(f"artifact version is immutable: {artifact_id} v{version}")
        directory.mkdir(parents=True)
        content_files: list[str] = []
        for filename, content in files.items():
            destination = (directory / filename).resolve()
            if directory.resolve() not in destination.parents:
                raise StoreError("artifact filename escapes artifact directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                destination.write_text(content)
            else:
                destination.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")
            content_files.append(str(destination.relative_to(self.root)))
        digest_input = {
            relative: digest_file(self.root / relative)
            for relative in sorted(content_files)
        }
        envelope = ArtifactEnvelope(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            version=version,
            run_id=self.run_id,
            stage=stage,
            producer_pair=producer_pair,
            lead=lead,
            peer=peer,
            input_digests=input_digests or {},
            content_files=content_files,
            content_digest=digest_value(digest_input),
            contract_item_ids=contract_item_ids or [],
            gate_status=gate_status,
        )
        self.atomic_json(directory.relative_to(self.root) / "envelope.json", envelope)
        self.append_log("artifacts", envelope.model_dump(mode="json"))
        return envelope

    def verify_artifact(self, envelope: ArtifactEnvelope) -> bool:
        digest_input: dict[str, str] = {}
        for relative in sorted(envelope.content_files):
            path = self._safe_path(relative)
            if not path.exists():
                return False
            digest_input[relative] = digest_file(path)
        return digest_value(digest_input) == envelope.content_digest

    def copy_into_bundle(self, sources: Iterable[tuple[Path, str]]) -> None:
        if self.bundle_dir.exists():
            shutil.rmtree(self.bundle_dir)
        self.bundle_dir.mkdir(parents=True)
        for source, relative_target in sources:
            target = (self.bundle_dir / relative_target).resolve()
            if self.bundle_dir.resolve() not in target.parents:
                raise StoreError("bundle target escapes bundle directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                ignore = (
                    shutil.ignore_patterns(
                        "node_modules",
                        ".git",
                        "*.tsbuildinfo",
                    )
                    if relative_target == "app"
                    else None
                )
                shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
            else:
                shutil.copy2(source, target)
