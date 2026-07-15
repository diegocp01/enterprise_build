from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zerohandoff.config import SettingsBundle, digest_value
from zerohandoff.delivery.bundle import BundleResult, DeliveryBundleAssembler
from zerohandoff.delivery.commands import CommandRunner
from zerohandoff.delivery.demo import DemoAssembler, DemoEvidence
from zerohandoff.delivery.gates import GateEngine
from zerohandoff.delivery.pairs import PairRunner
from zerohandoff.delivery.stages import STAGE_SPECS, contract_item_ids, stage_files
from zerohandoff.delivery.workspace import BuildEvidence, ReactViteWorkspaceBuilder
from zerohandoff.models import (
    ArtifactEnvelope,
    BuildRequest,
    DELIVERY_STAGES,
    FrozenRelationshipSnapshot,
    GateDecision,
    RepairPacket,
    RunManifest,
    RunStatus,
    Stage,
    utc_now,
)
from zerohandoff.runtime.base import RuntimeAdapter
from zerohandoff.storage import RunStore
from zerohandoff.trust import verify_frozen_snapshot


@dataclass(frozen=True)
class DeliveryResult:
    run_id: str
    status: RunStatus
    store_root: Path
    artifacts: dict[Stage, ArtifactEnvelope]
    bundle: BundleResult
    demo: DemoEvidence


class RunCancelled(RuntimeError):
    pass


class DeliveryOrchestrator:
    def __init__(
        self,
        *,
        settings: SettingsBundle,
        adapter: RuntimeAdapter,
        base_dir: Path | None = None,
        gate_engine: GateEngine | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.base_dir = (base_dir or settings.repo_root / ".zerohandoff" / "runs").resolve()
        self.gates = gate_engine or GateEngine()
        self.workspace_builder = ReactViteWorkspaceBuilder()
        self.demo_assembler = DemoAssembler()
        self.bundle_assembler = DeliveryBundleAssembler()

    def run(
        self,
        *,
        request: BuildRequest,
        frozen: FrozenRelationshipSnapshot,
        run_id: str | None = None,
        fault_stage: Stage | None = None,
        resume: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> DeliveryResult:
        if not verify_frozen_snapshot(frozen):
            raise ValueError("delivery requires a valid immutable relationship snapshot")
        intake_gate = self.gates.evaluate_intake(request)
        if intake_gate.decision != GateDecision.PASS:
            failed_rules = ", ".join(
                rule_id for rule_id, passed in intake_gate.rule_results.items() if not passed
            )
            raise ValueError(f"build request blocked by intake gate: {failed_rules}")
        run_id = run_id or f"delivery_{uuid.uuid4().hex[:12]}"
        store = RunStore(self.base_dir, run_id, "delivery")
        existing_state = store.read_state()
        if existing_state is not None:
            if not resume:
                raise ValueError(f"delivery run already exists: {run_id}")
            state, manifest, artifacts, previous, previous_digest = self._load_resume_context(
                store, request, frozen
            )
            state["status"] = RunStatus.RUNNING.value
            state.pop("failure_reason", None)
            state.pop("cancel_requested", None)
            store.write_state(state)
            store.append_event(
                event_type="delivery.resumed",
                status="running",
                stage=state.get("current_stage"),
                adapter=self.adapter.name,
                settings_digest=self.settings.digest,
                relationship_vector_digest=frozen.content_digest,
            )
        else:
            store.atomic_json("build_request.json", request)
            store.atomic_json("frozen_relationship_vectors.json", frozen)
            store.atomic_json("settings.snapshot.json", self.settings.snapshot())
            state = {
                "run_id": run_id,
                "status": RunStatus.RUNNING.value,
                "current_stage": Stage.INTAKE.value,
                "completed_stages": [],
                "settings_digest": self.settings.digest,
                "relationship_vector_digest": frozen.content_digest,
            }
            store.write_state(state)
            manifest = RunManifest(
                run_id=run_id,
                run_type="delivery",
                status=RunStatus.RUNNING,
                settings_digest=self.settings.digest,
                relationship_vector_digest=frozen.content_digest,
                adapter=self.adapter.name,
                model_settings=self.settings.models,
            )
            store.write_manifest(manifest)
            store.append_log("gates", intake_gate.model_dump(mode="json"))
            store.append_event(
                event_type="delivery.started",
                status="running",
                stage=Stage.INTAKE.value,
                adapter=self.adapter.name,
                settings_digest=self.settings.digest,
                relationship_vector_digest=frozen.content_digest,
                input_refs=["build_request.json", "frozen_relationship_vectors.json"],
            )
            store.append_event(
                event_type="delivery.intake.validated",
                status="completed",
                stage=Stage.INTAKE.value,
                settings_digest=self.settings.digest,
                relationship_vector_digest=frozen.content_digest,
                input_refs=["build_request.json"],
            )
            artifacts = {}
            previous = {}
            intake = store.commit_artifact(
                stage=Stage.INTAKE,
                artifact_id="build_request",
                artifact_type="build_request",
                version=1,
                producer_pair="HUMAN",
                lead="user",
                peer="system",
                files={"build_request.json": request.model_dump(mode="json")},
                contract_item_ids=[],
            )
            artifacts[Stage.INTAKE] = intake
            previous_digest = intake.content_digest
        pair_runner = PairRunner(
            settings=self.settings,
            adapter=self.adapter,
            store=store,
            frozen=frozen,
        )
        try:
            for stage in DELIVERY_STAGES:
                if stage.value in state["completed_stages"]:
                    continue
                self._check_cancel(cancel_check)
                self._verify_frozen_snapshot(frozen, stage)
                store.append_event(
                    event_type="delivery.frozen_snapshot.verified",
                    status="completed",
                    stage=stage.value,
                    relationship_vector_digest=frozen.content_digest,
                    settings_digest=self.settings.digest,
                )
                state["current_stage"] = stage.value
                store.write_state(state)
                store.append_event(
                    event_type="delivery.stage.started",
                    status="running",
                    stage=stage.value,
                    relationship_vector_digest=frozen.content_digest,
                    settings_digest=self.settings.digest,
                )
                pair_result = pair_runner.run(
                    stage=stage,
                    request=request,
                    previous=previous,
                    attempt=1,
                )
                candidate = dict(pair_result.candidate)
                build_evidence: BuildEvidence | None = None
                build_review_findings = []
                if stage == Stage.EXECUTE:
                    build_evidence, build_review_findings = self._build_workspace(
                        store,
                        request,
                        candidate,
                        pair_runner=pair_runner,
                        lead_name=pair_result.lead,
                        peer_name=pair_result.peer,
                    )
                    self._attach_build_evidence(candidate, build_evidence)
                if fault_stage == stage:
                    missing = STAGE_SPECS[stage].required_fields[0]
                    candidate.pop(missing, None)
                gate = self.gates.evaluate(
                    stage,
                    candidate,
                    [*pair_result.peer_findings, *build_review_findings],
                )
                store.append_log("gates", gate.model_dump(mode="json"))
                version = self._next_artifact_version(store, stage)
                repair_budget = self._repair_budget(stage)
                repairs_used = 0
                while gate.decision == GateDecision.REPAIR:
                    failed_envelope = self._commit_stage_artifact(
                        store,
                        stage,
                        version,
                        candidate,
                        pair_result.lead,
                        pair_result.peer,
                        previous_digest,
                        GateDecision.REPAIR,
                    )
                    store.append_event(
                        event_type="delivery.gate.repair",
                        status="repairing",
                        stage=stage.value,
                        attempt=repairs_used + 1,
                        output_refs=failed_envelope.content_files,
                        relationship_vector_digest=frozen.content_digest,
                        payload={
                            "findings": [finding.model_dump() for finding in gate.findings],
                            "repair_budget": repair_budget,
                            "repairs_used": repairs_used,
                        },
                    )
                    if repairs_used >= repair_budget:
                        raise RuntimeError(f"repair budget exhausted for {stage.value}")
                    repairs_used += 1
                    state["status"] = RunStatus.REPAIRING.value
                    store.write_state(state)
                    packet = RepairPacket(
                        repair_id=f"repair_{uuid.uuid4().hex[:10]}",
                        run_id=run_id,
                        stage=stage,
                        attempt=repairs_used + 1,
                        remaining_budget=repair_budget - repairs_used,
                        findings=gate.findings,
                        artifact_refs=failed_envelope.content_files,
                        expected_condition="All deterministic gate rules pass.",
                    )
                    store.append_log("repairs", packet.model_dump(mode="json"))
                    candidate = pair_runner.repair(
                        packet=packet,
                        request=request,
                        previous=previous,
                        lead_name=pair_result.lead,
                    )
                    if stage == Stage.EXECUTE:
                        build_evidence, build_review_findings = self._verify_build_workspace(
                            store,
                            candidate,
                            pair_runner=pair_runner,
                            lead_name=pair_result.lead,
                            peer_name=pair_result.peer,
                            attempt=packet.attempt,
                        )
                        self._attach_build_evidence(candidate, build_evidence)
                    gate = self.gates.evaluate(stage, candidate, build_review_findings)
                    store.append_log("gates", gate.model_dump(mode="json"))
                    version += 1
                    manifest.repair_counts[stage.value] = repairs_used
                if gate.decision != GateDecision.PASS:
                    raise RuntimeError(f"gate failed after bounded repair: {stage.value}")
                state["status"] = RunStatus.RUNNING.value
                envelope = self._commit_stage_artifact(
                    store,
                    stage,
                    version,
                    candidate,
                    pair_result.lead,
                    pair_result.peer,
                    previous_digest,
                    GateDecision.PASS,
                )
                artifacts[stage] = envelope
                previous[stage] = candidate
                previous_digest = envelope.content_digest
                state["completed_stages"].append(stage.value)
                store.write_state(state)
                manifest.stage_outcomes[stage.value] = "PASS"
                store.append_event(
                    event_type="delivery.stage.completed",
                    status="completed",
                    stage=stage.value,
                    attempt=version,
                    relationship_vector_digest=frozen.content_digest,
                    output_refs=envelope.content_files,
                    payload={"artifact_digest": envelope.content_digest},
                )
            self._check_cancel(cancel_check)
            self._verify_frozen_snapshot(frozen, Stage.DEMO)
            observe = previous[Stage.OBSERVE]
            state["current_stage"] = Stage.DEMO.value
            store.write_state(state)
            if Stage.DEMO.value in state["completed_stages"]:
                demo = self._load_demo_evidence(store, artifacts[Stage.DEMO])
            else:
                demo = self.demo_assembler.assemble(
                    store=store,
                    preview_html=store.workspace_dir / "app" / "dist" / "index.html",
                    demo_plan=observe["demo_plan"],
                    narration_script=observe["narration_script"],
                    max_seconds=int(self.settings.delivery["max_demo_seconds"]),
                )
                demo_envelope = store.commit_artifact(
                    stage=Stage.DEMO,
                    artifact_id="narrated_demo",
                    artifact_type="demo",
                    version=self._next_artifact_version(store, Stage.DEMO),
                    producer_pair="OBSERVE",
                    lead=artifacts[Stage.OBSERVE].lead,
                    peer=artifacts[Stage.OBSERVE].peer,
                    files={
                        "demo_evidence.json": {
                            "video": str(demo.video_path.relative_to(store.root)),
                            "screenshot": str(demo.screenshot_path.relative_to(store.root)),
                            "duration_seconds": demo.duration_seconds,
                            "has_video": demo.has_video,
                            "has_audio": demo.has_audio,
                            "checksum": demo.checksum,
                        }
                    },
                    input_digests={"observe": artifacts[Stage.OBSERVE].content_digest},
                    contract_item_ids=contract_item_ids(observe),
                )
                artifacts[Stage.DEMO] = demo_envelope
                state["completed_stages"].append(Stage.DEMO.value)
                store.write_state(state)
                store.append_log(
                    "gates",
                    {
                        "stage": Stage.DEMO.value,
                        "decision": GateDecision.PASS.value,
                        "rule_results": {
                            "demo.video_decodes": demo.has_video,
                            "demo.audio_present": demo.has_audio,
                            "demo.duration_below_limit": (
                                demo.duration_seconds
                                < int(self.settings.delivery["max_demo_seconds"])
                            ),
                            "demo.checksum_present": bool(demo.checksum),
                        },
                        "findings": [],
                        "evidence": [
                            str(demo.video_path.relative_to(store.root)),
                            str(demo.screenshot_path.relative_to(store.root)),
                        ],
                    },
                )
            self._check_cancel(cancel_check)
            manifest.stage_outcomes[Stage.DEMO.value] = "PASS"
            manifest.status = RunStatus.COMPLETED
            manifest.completed_at = utc_now()
            manifest.final_outcome = "delivery_bundle_ready"
            manifest.artifact_checksums = {
                stage.value: envelope.content_digest for stage, envelope in artifacts.items()
            }
            store.write_manifest(manifest)
            bundle = self.bundle_assembler.assemble(store)
            state["current_stage"] = Stage.BUNDLE.value
            if Stage.BUNDLE.value not in state["completed_stages"]:
                state["completed_stages"].append(Stage.BUNDLE.value)
            state["bundle"] = str(bundle.bundle_dir.relative_to(store.root))
            store.write_state(state)
            store.append_event(
                event_type="delivery.completed",
                status="completed",
                stage=Stage.BUNDLE.value,
                relationship_vector_digest=frozen.content_digest,
                output_refs=[str(bundle.bundle_dir.relative_to(store.root))],
            )
            # Refresh the bundle once after terminal evidence is committed so its copied
            # event stream and checksums include the final bundle and run outcomes.
            bundle = self.bundle_assembler.assemble(store, record_event=False)
            state["status"] = RunStatus.COMPLETED.value
            store.write_state(state)
            return DeliveryResult(run_id, RunStatus.COMPLETED, store.root, artifacts, bundle, demo)
        except RunCancelled as exc:
            self._record_cancelled(store, state, manifest, frozen, str(exc))
            raise
        except Exception as exc:
            if cancel_check and cancel_check():
                cancelled = RunCancelled("delivery cancelled by emergency stop")
                self._record_cancelled(store, state, manifest, frozen, str(cancelled))
                raise cancelled from exc
            state["status"] = RunStatus.FAILED.value
            state["failure_reason"] = str(exc)
            store.write_state(state)
            manifest.status = RunStatus.FAILED
            manifest.completed_at = utc_now()
            manifest.failure_reason = str(exc)
            manifest.final_outcome = "failed"
            store.write_manifest(manifest)
            store.append_event(
                event_type="delivery.failed",
                status="failed",
                stage=state.get("current_stage"),
                relationship_vector_digest=frozen.content_digest,
                error={"code": type(exc).__name__, "message": str(exc)},
            )
            raise

    @staticmethod
    def _check_cancel(cancel_check: Callable[[], bool] | None) -> None:
        if cancel_check and cancel_check():
            raise RunCancelled("delivery cancelled by emergency stop")

    @staticmethod
    def _verify_frozen_snapshot(
        frozen: FrozenRelationshipSnapshot,
        stage: Stage,
    ) -> None:
        if not verify_frozen_snapshot(frozen):
            raise ValueError(f"frozen relationship snapshot changed before {stage.value}")

    @staticmethod
    def _record_cancelled(
        store: RunStore,
        state: dict[str, Any],
        manifest: RunManifest,
        frozen: FrozenRelationshipSnapshot,
        reason: str,
    ) -> None:
        state["status"] = RunStatus.CANCELLED.value
        state["failure_reason"] = reason
        store.write_state(state)
        manifest.status = RunStatus.CANCELLED
        manifest.completed_at = utc_now()
        manifest.failure_reason = reason
        manifest.final_outcome = "cancelled"
        store.write_manifest(manifest)
        store.append_event(
            event_type="delivery.cancelled",
            status="cancelled",
            stage=state.get("current_stage"),
            relationship_vector_digest=frozen.content_digest,
        )

    def _load_resume_context(
        self,
        store: RunStore,
        request: BuildRequest,
        frozen: FrozenRelationshipSnapshot,
    ) -> tuple[
        dict[str, Any],
        RunManifest,
        dict[Stage, ArtifactEnvelope],
        dict[Stage, dict[str, Any]],
        str,
    ]:
        state = store.read_state()
        assert state is not None
        if state.get("status") == RunStatus.COMPLETED.value:
            raise ValueError("a completed delivery run cannot be resumed")
        stored_request = json.loads((store.root / "build_request.json").read_text())
        if digest_value(stored_request) != digest_value(request):
            raise ValueError("build request changed; refusing to resume")
        stored_frozen = FrozenRelationshipSnapshot.model_validate_json(
            (store.root / "frozen_relationship_vectors.json").read_text()
        )
        if stored_frozen.content_digest != frozen.content_digest:
            raise ValueError("frozen relationship snapshot changed; refusing to resume")
        if state.get("settings_digest") != self.settings.digest:
            raise ValueError("settings changed; refusing to resume")
        manifest = RunManifest.model_validate_json((store.root / "manifest.json").read_text())
        manifest.status = RunStatus.RUNNING
        manifest.completed_at = None
        manifest.failure_reason = None
        manifest.final_outcome = None
        rows = RunStore.read_jsonl(store.logs_dir / "artifacts.jsonl")
        best: dict[Stage, ArtifactEnvelope] = {}
        for row in rows:
            payload = {
                key: value for key, value in row.items() if key not in {"phase", "timestamp"}
            }
            envelope = ArtifactEnvelope.model_validate(payload)
            if envelope.gate_status != GateDecision.PASS:
                continue
            current = best.get(envelope.stage)
            if current is None or envelope.version > current.version:
                best[envelope.stage] = envelope
        if Stage.INTAKE not in best or not store.verify_artifact(best[Stage.INTAKE]):
            raise ValueError("resume boundary has no valid intake artifact")
        previous: dict[Stage, dict[str, Any]] = {}
        previous_digest = best[Stage.INTAKE].content_digest
        completed = set(state.get("completed_stages", []))
        for stage in DELIVERY_STAGES:
            if stage.value not in completed:
                continue
            envelope = best.get(stage)
            if envelope is None or not store.verify_artifact(envelope):
                raise ValueError(f"resume boundary has an invalid {stage.value} artifact")
            json_file = next(
                (relative for relative in envelope.content_files if relative.endswith(".json")), None
            )
            if json_file is None:
                raise ValueError(f"resume boundary has no JSON payload for {stage.value}")
            previous[stage] = json.loads((store.root / json_file).read_text())
            previous_digest = envelope.content_digest
        if Stage.DEMO.value in completed:
            envelope = best.get(Stage.DEMO)
            if envelope is None or not store.verify_artifact(envelope):
                raise ValueError("resume boundary has an invalid DEMO artifact")
        return state, manifest, best, previous, previous_digest

    @staticmethod
    def _next_artifact_version(store: RunStore, stage: Stage) -> int:
        versions = [
            int(row["version"])
            for row in RunStore.read_jsonl(store.logs_dir / "artifacts.jsonl")
            if row.get("stage") == stage.value
        ]
        return max(versions, default=0) + 1

    @staticmethod
    def _load_demo_evidence(store: RunStore, envelope: ArtifactEnvelope) -> DemoEvidence:
        json_file = next(relative for relative in envelope.content_files if relative.endswith(".json"))
        payload = json.loads((store.root / json_file).read_text())
        return DemoEvidence(
            video_path=store.root / payload["video"],
            narration_path=store.root / "demo" / "narration.txt",
            screenshot_path=store.root / payload["screenshot"],
            duration_seconds=float(payload["duration_seconds"]),
            has_video=bool(payload["has_video"]),
            has_audio=bool(payload["has_audio"]),
            checksum=str(payload["checksum"]),
        )

    def _build_workspace(
        self,
        store: RunStore,
        request: BuildRequest,
        candidate: dict[str, Any],
        *,
        pair_runner: PairRunner,
        lead_name: str,
        peer_name: str,
    ) -> tuple[BuildEvidence, list[Any]]:
        app_dir = store.workspace_dir / "app"
        if app_dir.exists():
            shutil.rmtree(app_dir)
        evidence = self.workspace_builder.build(app_dir, request, candidate)
        store.atomic_json("workspace/build_commands.snapshot.json", evidence.commands)
        implementer_report = pair_runner.implement_workspace(
            workspace=app_dir,
            request=request,
            candidate=candidate,
            lead_name=lead_name,
        )
        candidate["implementer_report"] = implementer_report
        return self._verify_build_workspace(
            store,
            candidate,
            pair_runner=pair_runner,
            lead_name=lead_name,
            peer_name=peer_name,
            attempt=1,
        )

    def _verify_build_workspace(
        self,
        store: RunStore,
        candidate: dict[str, Any],
        *,
        pair_runner: PairRunner,
        lead_name: str,
        peer_name: str,
        attempt: int,
    ) -> tuple[BuildEvidence, list[Any]]:
        app_dir = store.workspace_dir / "app"
        commands = json.loads(
            (store.workspace_dir / "build_commands.snapshot.json").read_text()
        )
        evidence = self.workspace_builder.inspect(app_dir, candidate, commands=commands)
        command_names = ["test"] if self.adapter.name == "fixture" else [
            "install",
            "test",
            "typecheck",
            "build",
        ]
        commands = [evidence.commands[name] for name in command_names]
        command_results = CommandRunner(
            workspace=app_dir,
            allowed_commands=set(commands),
            store=store,
        ).run_many(commands, timeout_seconds=int(self.settings.training["timeout_seconds"]))
        evidence = self.workspace_builder.inspect(
            app_dir,
            candidate,
            commands=evidence.commands,
            command_results=tuple(result.__dict__ for result in command_results),
        )
        if not evidence.passed:
            raise RuntimeError("generated application failed structural or command build checks")
        review_candidate = dict(candidate)
        self._attach_build_evidence(review_candidate, evidence)
        findings = pair_runner.review_workspace(
            workspace=app_dir,
            candidate=review_candidate,
            implementer_report=candidate.get(
                "implementer_report",
                {
                    "summary": "Bounded EXECUTE repair completed.",
                    "changed_files": [],
                    "contract_item_ids": candidate.get("contract_item_ids", []),
                },
            ),
            peer_name=peer_name,
            lead_name=lead_name,
            attempt=attempt,
        )
        return evidence, findings

    @staticmethod
    def _attach_build_evidence(candidate: dict[str, Any], evidence: BuildEvidence) -> None:
        candidate["files"] = evidence.files
        candidate["commands"] = evidence.commands
        candidate["build_evidence"] = {
            "profile": evidence.profile,
            "checks": evidence.checks,
            "file_digests": evidence.file_digests,
            "command_results": evidence.command_results,
            "passed": evidence.passed,
        }

    def _commit_stage_artifact(
        self,
        store: RunStore,
        stage: Stage,
        version: int,
        candidate: dict[str, Any],
        lead: str,
        peer: str,
        previous_digest: str,
        gate_status: GateDecision,
    ) -> ArtifactEnvelope:
        spec = STAGE_SPECS[stage]
        return store.commit_artifact(
            stage=stage,
            artifact_id=spec.artifact_id,
            artifact_type=spec.artifact_type,
            version=version,
            producer_pair=stage.value,
            lead=lead,
            peer=peer,
            files=stage_files(spec, candidate),
            input_digests={"previous": previous_digest, "candidate": digest_value(candidate)},
            contract_item_ids=contract_item_ids(candidate),
            gate_status=gate_status,
        )

    def _repair_budget(self, stage: Stage) -> int:
        if stage == Stage.EXECUTE:
            return int(self.settings.delivery["execute_repairs"])
        if stage == Stage.OBSERVE:
            return int(self.settings.delivery["observe_repairs"])
        return int(self.settings.delivery["artifact_stage_repairs"])
