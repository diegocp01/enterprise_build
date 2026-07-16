from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from zerohandoff.config import SettingsBundle
from zerohandoff.continual import InferenceLearningStore
from zerohandoff.delivery.bundle import DeliveryBundleAssembler
from zerohandoff.delivery.demo import DemoAssembler
from zerohandoff.delivery.orchestrator import DeliveryOrchestrator, DeliveryResult
from zerohandoff.delivery.stages import contract_item_ids
from zerohandoff.doctor import doctor
from zerohandoff.models import (
    BuildRequest,
    ArtifactEnvelope,
    FrozenRelationshipSnapshot,
    InferenceLearningState,
    RunStatus,
    Stage,
)
from zerohandoff.runtime.base import RuntimeAdapter
from zerohandoff.runtime.codex import CodexExecAdapter
from zerohandoff.runtime.fixture import FixtureAdapter
from zerohandoff.storage import RunStore
from zerohandoff.training.orchestrator import TrainingOrchestrator, TrainingResult
from zerohandoff.training.puzzles import PuzzleRepository
from zerohandoff.trust import verify_frozen_snapshot


AdapterName = Literal["fixture", "codex"]


class RunService:
    """Application boundary shared by Codex skills, the API, and tests."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.settings = SettingsBundle.load(self.repo_root)
        self.system_root = self.repo_root / ".zerohandoff"
        self.training_root = self.system_root / "training"
        self.delivery_root = self.system_root / "runs"
        self.frozen_root = self.system_root / "frozen"
        self.learning_root = self.system_root / "learning"
        for directory in (
            self.training_root,
            self.delivery_root,
            self.frozen_root,
            self.learning_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._cancel_events: dict[str, threading.Event] = {}
        self._active_adapters: dict[str, RuntimeAdapter] = {}
        self._lock = threading.Lock()

    def adapter(self, name: AdapterName, run_id: str) -> RuntimeAdapter:
        if name == "fixture":
            return FixtureAdapter()
        return CodexExecAdapter(self.system_root / "raw" / run_id)

    def readiness(self) -> dict[str, Any]:
        report = doctor(self.repo_root)
        latest = self.frozen_root / "latest.json"
        report["frozen_snapshot"] = {
            "ready": latest.exists(),
            "path": str(latest) if latest.exists() else None,
        }
        inference = self.learning_root / "inference_relationships.json"
        report["inference_learning"] = {
            "ready": inference.exists(),
            "path": str(inference) if inference.exists() else None,
        }
        return report

    def train(
        self,
        *,
        adapter: AdapterName = "fixture",
        rounds: int | None = None,
        run_id: str | None = None,
    ) -> TrainingResult:
        run_id = run_id or f"train_{uuid.uuid4().hex[:12]}"
        runtime = self.adapter(adapter, run_id)
        puzzles = self.puzzle_repository(adapter)
        result = TrainingOrchestrator(
            settings=self.settings,
            adapter=runtime,
            puzzle_repository=puzzles,
            base_dir=self.training_root,
        ).run(run_id=run_id, rounds=rounds)
        if result.frozen_snapshot is not None:
            self.publish_frozen(result.frozen_snapshot)
        return result

    def puzzle_repository(self, adapter: AdapterName) -> PuzzleRepository:
        filename = "puzzles.fixture.json" if adapter == "fixture" else "puzzles.jsonl"
        path = self.repo_root / "data" / filename
        if not path.exists():
            raise FileNotFoundError(f"puzzle corpus is missing: {path}")
        return PuzzleRepository.load(path)

    def publish_frozen(self, snapshot: FrozenRelationshipSnapshot) -> Path:
        if not verify_frozen_snapshot(snapshot):
            raise ValueError("refusing to publish an invalid frozen snapshot")
        payload = json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        destination = self.frozen_root / f"{snapshot.training_run_id}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(payload)
        temporary.replace(destination)
        latest = self.frozen_root / "latest.json"
        latest_tmp = self.frozen_root / ".latest.tmp"
        latest_tmp.write_text(payload)
        latest_tmp.replace(latest)
        return destination

    def load_frozen(self, path: Path | None = None) -> FrozenRelationshipSnapshot:
        selected = (path or self.frozen_root / "latest.json").resolve()
        if self.repo_root != selected and self.repo_root not in selected.parents:
            raise ValueError("frozen snapshot must be inside the repository")
        if not selected.exists():
            raise FileNotFoundError(
                "no frozen relationship snapshot is available; run the training workflow first"
            )
        snapshot = FrozenRelationshipSnapshot.model_validate_json(selected.read_text())
        if not verify_frozen_snapshot(snapshot):
            raise ValueError("frozen relationship snapshot failed its digest check")
        return snapshot

    def deliver(
        self,
        *,
        request: BuildRequest,
        adapter: AdapterName = "fixture",
        frozen_path: Path | None = None,
        run_id: str | None = None,
        fault_stage: Stage | None = None,
        resume: bool = False,
    ) -> DeliveryResult:
        run_id = run_id or f"delivery_{uuid.uuid4().hex[:12]}"
        event = self._event_for(run_id)
        runtime = self.adapter(adapter, run_id)
        with self._lock:
            self._active_adapters[run_id] = runtime
        try:
            return DeliveryOrchestrator(
                settings=self.settings,
                adapter=runtime,
                base_dir=self.delivery_root,
            ).run(
                request=request,
                frozen=self.load_frozen(frozen_path),
                run_id=run_id,
                fault_stage=fault_stage,
                resume=resume,
                cancel_check=event.is_set,
            )
        finally:
            with self._lock:
                self._cancel_events.pop(run_id, None)
                self._active_adapters.pop(run_id, None)

    def invalidate_learning_commit(self, run_id: str, *, reason: str) -> dict[str, Any]:
        """Preserve and invalidate a proven-bad latest learning commit for one run."""

        store = RunStore(self.delivery_root, run_id, "delivery")
        start_path = store.root / "inference_relationships.start.json"
        end_path = store.root / "inference_relationships.end.json"
        if not start_path.exists() or not end_path.exists():
            raise FileNotFoundError("run does not contain both inference start and end states")
        start = InferenceLearningState.model_validate_json(start_path.read_text())
        end = InferenceLearningState.model_validate_json(end_path.read_text())
        record = InferenceLearningStore(self.learning_root).invalidate_latest_commit(
            run_id=run_id,
            start_state=start,
            invalid_end_digest=end.content_digest,
            reason=reason,
        )
        suffix = end.content_digest.removeprefix("sha256:")[:12]
        preserved: list[str] = []
        for name in (
            "inference_relationships.end.json",
            "inference_relationship_deltas.json",
            "inference_night_commit.json",
        ):
            source = store.root / name
            if source.exists():
                target = source.with_name(f"{source.stem}.superseded_{suffix}{source.suffix}")
                source.replace(target)
                preserved.append(str(target.relative_to(store.root)))
        state = store.read_state() or {"run_id": run_id}
        state.update(
            {
                "status": RunStatus.FAILED.value,
                "current_stage": Stage.DEMO.value,
                "failure_reason": reason,
                "learning_commit_sequence": start.commit_sequence,
                "relationship_vector_digest": start.content_digest,
            }
        )
        store.write_state(state)
        manifest_path = store.root / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            manifest.update(
                {
                    "status": RunStatus.FAILED.value,
                    "failure_reason": reason,
                    "final_outcome": "learning_commit_invalidated",
                }
            )
            store.atomic_json("manifest.json", manifest)
        store.append_event(
            event_type="delivery.inference_learning.invalidated",
            status="failed",
            stage="NIGHT_COMMIT",
            relationship_vector_digest=start.content_digest,
            output_refs=preserved,
            payload=record,
        )
        return {**record, "preserved_run_files": preserved}

    def repair_demo(self, run_id: str) -> dict[str, Any]:
        """Regenerate a completed run's demo without changing its learning commit."""

        store = RunStore(self.delivery_root, run_id, "delivery")
        state = store.read_state() or {}
        if state.get("status") != RunStatus.COMPLETED.value:
            raise RuntimeError("demo repair requires a completed delivery run")
        artifact_rows = RunStore.read_jsonl(store.logs_dir / "artifacts.jsonl")
        observe_rows = [
            row
            for row in artifact_rows
            if row.get("stage") == Stage.OBSERVE.value
            and row.get("gate_status") == "PASS"
        ]
        if not observe_rows:
            raise FileNotFoundError("completed OBSERVE artifact is missing")
        observe_row = max(observe_rows, key=lambda row: int(row["version"]))
        observe_envelope = ArtifactEnvelope.model_validate(
            {
                key: value
                for key, value in observe_row.items()
                if key not in {"phase", "timestamp"}
            }
        )
        observe_json = next(
            relative
            for relative in observe_envelope.content_files
            if relative.endswith("evidence_and_learning.json")
        )
        observe = json.loads((store.root / observe_json).read_text())
        demo = DemoAssembler().assemble(
            store=store,
            preview_html=store.workspace_dir / "app" / "dist" / "index.html",
            demo_plan=observe["demo_plan"],
            narration_script=observe["narration_script"],
            max_seconds=int(self.settings.delivery["max_demo_seconds"]),
        )
        demo_versions = [
            int(row["version"])
            for row in artifact_rows
            if row.get("stage") == Stage.DEMO.value
        ]
        demo_envelope = store.commit_artifact(
            stage=Stage.DEMO,
            artifact_id="narrated_demo",
            artifact_type="demo",
            version=max(demo_versions, default=0) + 1,
            producer_pair="OBSERVE",
            lead=observe_envelope.lead,
            peer=observe_envelope.peer,
            files={
                "demo_evidence.json": {
                    "video": str(demo.video_path.relative_to(store.root)),
                    "screenshot": str(demo.screenshot_path.relative_to(store.root)),
                    "duration_seconds": demo.duration_seconds,
                    "has_video": demo.has_video,
                    "has_audio": demo.has_audio,
                    "visual_content": demo.visual_content,
                    "capture_mode": demo.capture_mode,
                    "checksum": demo.checksum,
                }
            },
            input_digests={"observe": observe_envelope.content_digest},
            contract_item_ids=contract_item_ids(observe),
        )
        store.append_log(
            "gates",
            {
                "stage": Stage.DEMO.value,
                "decision": "PASS",
                "rule_results": {
                    "demo.video_decodes": demo.has_video,
                    "demo.audio_present": demo.has_audio,
                    "demo.visual_content": demo.visual_content,
                    "demo.browser_capture": demo.capture_mode == "browser",
                    "demo.duration_below_limit": (
                        demo.duration_seconds
                        < int(self.settings.delivery["max_demo_seconds"])
                    ),
                },
                "findings": [],
                "evidence": [
                    str(demo.video_path.relative_to(store.root)),
                    str(demo.screenshot_path.relative_to(store.root)),
                ],
            },
        )
        store.append_event(
            event_type="delivery.demo.repaired",
            status="completed",
            stage=Stage.DEMO.value,
            output_refs=demo_envelope.content_files,
            payload={
                "capture_mode": demo.capture_mode,
                "visual_content": demo.visual_content,
                "checksum": demo.checksum,
            },
        )
        manifest_path = store.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.setdefault("artifact_checksums", {})[Stage.DEMO.value] = (
            demo_envelope.content_digest
        )
        store.atomic_json("manifest.json", manifest)
        bundle = DeliveryBundleAssembler().assemble(store, record_event=False)
        return {
            "run_id": run_id,
            "demo_version": demo_envelope.version,
            "video": str(demo.video_path),
            "screenshot": str(demo.screenshot_path),
            "capture_mode": demo.capture_mode,
            "visual_content": demo.visual_content,
            "checksum": demo.checksum,
            "bundle": str(bundle.bundle_dir),
        }

    def _event_for(self, run_id: str) -> threading.Event:
        with self._lock:
            return self._cancel_events.setdefault(run_id, threading.Event())

    def cancel(self, run_id: str) -> dict[str, Any]:
        event = self._event_for(run_id)
        event.set()
        with self._lock:
            runtime = self._active_adapters.get(run_id)
        cancelled_invocations = 0
        if runtime is not None and hasattr(runtime, "cancel_all"):
            cancelled_invocations = int(runtime.cancel_all())  # type: ignore[attr-defined]
        root = self._delivery_path(run_id)
        if root.exists():
            store = RunStore(self.delivery_root, run_id, "delivery")
            state = store.read_state() or {"run_id": run_id}
            if state.get("status") not in {
                RunStatus.COMPLETED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
            }:
                state["cancel_requested"] = True
                store.write_state(state)
                store.append_event(
                    event_type="delivery.cancel.requested",
                    status="running",
                    stage=state.get("current_stage"),
                )
        return {
            "run_id": run_id,
            "cancel_requested": True,
            "cancelled_invocations": cancelled_invocations,
        }

    def list_runs(self) -> list[dict[str, Any]]:
        run_paths = [
            path
            for path in self.delivery_root.iterdir()
            if path.is_dir()
            and ((path / "state.json").exists() or (path / "manifest.json").exists())
        ]
        runs = [self._summary(path.name) for path in run_paths]
        return sorted(runs, key=lambda item: item.get("updated_at", ""), reverse=True)

    def run_summary(self, run_id: str) -> dict[str, Any]:
        return self._summary(run_id)

    def _summary(self, run_id: str) -> dict[str, Any]:
        root = self._delivery_path(run_id)
        if not root.exists():
            raise FileNotFoundError(f"unknown delivery run: {run_id}")
        state_path = root / "state.json"
        manifest_path = root / "manifest.json"
        request_path = root / "build_request.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        build_request = json.loads(request_path.read_text()) if request_path.exists() else None
        events = RunStore.read_jsonl(root / "events.jsonl")
        repairs = RunStore.read_jsonl(root / "logs" / "repairs.jsonl")
        gates = RunStore.read_jsonl(root / "logs" / "gates.jsonl")
        calls = RunStore.read_jsonl(root / "logs" / "agent_calls.jsonl")
        handoffs = RunStore.read_jsonl(root / "logs" / "handoff_rewards.jsonl")
        shadows = RunStore.read_jsonl(root / "logs" / "shadow_trust_updates.jsonl")
        night_commits = RunStore.read_jsonl(
            root / "logs" / "inference_night_commits.jsonl"
        )
        learning_end_path = root / "inference_relationships.end.json"
        learning_end = (
            json.loads(learning_end_path.read_text()) if learning_end_path.exists() else {}
        )
        return {
            "run_id": run_id,
            "status": state.get("status", manifest.get("status", "unknown")),
            "current_stage": state.get("current_stage"),
            "completed_stages": state.get("completed_stages", []),
            "relationship_vector_digest": manifest.get("relationship_vector_digest"),
            "baseline_relationship_digest": state.get("baseline_relationship_digest"),
            "inference_end_digest": learning_end.get("content_digest"),
            "learning_commit_sequence": learning_end.get("commit_sequence"),
            "adapter": manifest.get("adapter"),
            "build_request": build_request,
            "repair_count": len(repairs),
            "gate_count": len(gates),
            "agent_call_count": len(calls),
            "handoff_reward_count": len(handoffs),
            "shadow_trust_update_count": len(shadows),
            "night_commit_count": len(night_commits),
            "event_count": len(events),
            "failure_reason": state.get("failure_reason") or manifest.get("failure_reason"),
            "bundle_ready": (root / "delivery_bundle" / "delivery_manifest.json").exists(),
            "preview_ready": (root / "workspace" / "app" / "dist" / "index.html").exists(),
            "video_ready": (root / "demo" / "demo.mp4").exists(),
            "updated_at": state_path.stat().st_mtime_ns if state_path.exists() else 0,
        }

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return RunStore.read_jsonl(self._delivery_path(run_id) / "events.jsonl")

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return RunStore.read_jsonl(self._delivery_path(run_id) / "logs" / "artifacts.jsonl")

    def evidence(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        root = self._delivery_path(run_id)
        if not root.exists():
            raise FileNotFoundError(f"unknown delivery run: {run_id}")
        return {
            category: RunStore.read_jsonl(root / "logs" / f"{category}.jsonl")
            for category in (
                "artifacts",
                "gates",
                "repairs",
                "commands",
                "agent_calls",
                "demo",
                "handoff_rewards",
                "shadow_trust_updates",
                "inference_night_commits",
            )
        }

    def bundle_archive(self, run_id: str) -> Path:
        root = self._delivery_path(run_id)
        bundle = root / "delivery_bundle"
        if not (bundle / "delivery_manifest.json").exists():
            raise FileNotFoundError("delivery bundle is not ready")
        archive = root / "delivery_bundle.zip"
        if not archive.exists() or archive.stat().st_mtime_ns < bundle.stat().st_mtime_ns:
            created = shutil.make_archive(str(archive.with_suffix("")), "zip", bundle)
            archive = Path(created)
        return archive

    def _delivery_path(self, run_id: str) -> Path:
        if not run_id or any(
            ch not in "-_.abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            for ch in run_id
        ):
            raise ValueError("invalid delivery run id")
        path = (self.delivery_root / run_id).resolve()
        if self.delivery_root.resolve() not in path.parents:
            raise ValueError("run path escapes the delivery root")
        return path
