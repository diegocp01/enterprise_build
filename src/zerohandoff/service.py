from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from zerohandoff.config import SettingsBundle
from zerohandoff.delivery.orchestrator import DeliveryOrchestrator, DeliveryResult
from zerohandoff.doctor import doctor
from zerohandoff.models import BuildRequest, FrozenRelationshipSnapshot, RunStatus, Stage
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
        for directory in (self.training_root, self.delivery_root, self.frozen_root):
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
        return {
            "run_id": run_id,
            "status": state.get("status", manifest.get("status", "unknown")),
            "current_stage": state.get("current_stage"),
            "completed_stages": state.get("completed_stages", []),
            "relationship_vector_digest": manifest.get("relationship_vector_digest"),
            "adapter": manifest.get("adapter"),
            "build_request": build_request,
            "repair_count": len(repairs),
            "gate_count": len(gates),
            "agent_call_count": len(calls),
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
            for category in ("artifacts", "gates", "repairs", "commands", "agent_calls", "demo")
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
