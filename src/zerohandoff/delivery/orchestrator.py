from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from zerohandoff.config import SettingsBundle, digest_file, digest_value
from zerohandoff.continual import (
    InferenceLearningStore,
    propose_handoff_updates,
    sign_state,
    verify_inference_state,
)
from zerohandoff.delivery.bundle import BundleResult, DeliveryBundleAssembler
from zerohandoff.delivery.commands import CommandRunner, execute_validation_commands
from zerohandoff.delivery.demo import (
    DemoAssembler,
    DemoEvidence,
    is_presenter_quality_narration,
)
from zerohandoff.delivery.gates import GateEngine
from zerohandoff.delivery.pairs import PairResult, PairRunner
from zerohandoff.delivery.stages import STAGE_SPECS, contract_item_ids, stage_files
from zerohandoff.delivery.workspace import BuildEvidence, ReactViteWorkspaceBuilder
from zerohandoff.models import (
    ArtifactEnvelope,
    BuildRequest,
    DELIVERY_STAGES,
    FrozenRelationshipSnapshot,
    GateDecision,
    GateResult,
    HandoffAssessment,
    InferenceLearningState,
    RepairPacket,
    ReviewFinding,
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
        self.learning_store = InferenceLearningStore(self.base_dir.parent / "learning")

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
        learning_already_committed = False
        committed_learning: InferenceLearningState | None = None
        if existing_state is not None:
            if not resume:
                raise ValueError(f"delivery run already exists: {run_id}")
            state, manifest, artifacts, previous, previous_digest = self._load_resume_context(
                store, request, frozen
            )
            learning_start = InferenceLearningState.model_validate_json(
                (store.root / "inference_relationships.start.json").read_text()
            )
            if not verify_inference_state(learning_start):
                raise ValueError("run-start inference relationship state failed its digest check")
            current_learning = self.learning_store.load_or_initialize(
                frozen,
                self.settings.agents,
                self.settings.continual_learning,
            )
            end_state_path = store.root / "inference_relationships.end.json"
            if end_state_path.exists():
                committed_learning = InferenceLearningState.model_validate_json(
                    end_state_path.read_text()
                )
                learning_already_committed = (
                    verify_inference_state(committed_learning)
                    and current_learning.content_digest
                    == committed_learning.content_digest
                    and committed_learning.commit_sequence
                    == learning_start.commit_sequence + 1
                )
            if (
                current_learning.content_digest != learning_start.content_digest
                and not learning_already_committed
            ):
                raise RuntimeError(
                    "cannot resume: another prototype already advanced inference learning"
                )
            previous_digest = self._reconcile_resume_workspace_drift(
                store=store,
                state=state,
                manifest=manifest,
                artifacts=artifacts,
                previous=previous,
                previous_digest=previous_digest,
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
                relationship_vector_digest=learning_start.content_digest,
            )
        else:
            learning_start = self.learning_store.load_or_initialize(
                frozen,
                self.settings.agents,
                self.settings.continual_learning,
            ).model_copy(deep=True)
            store.atomic_json("build_request.json", request)
            store.atomic_json("frozen_relationship_vectors.json", frozen)
            store.atomic_json("inference_relationships.start.json", learning_start)
            store.atomic_json("settings.snapshot.json", self.settings.snapshot())
            state = {
                "run_id": run_id,
                "status": RunStatus.RUNNING.value,
                "current_stage": Stage.INTAKE.value,
                "completed_stages": [],
                "settings_digest": self.settings.digest,
                "relationship_vector_digest": learning_start.content_digest,
                "baseline_relationship_digest": frozen.content_digest,
                "learning_commit_sequence": learning_start.commit_sequence,
            }
            store.write_state(state)
            manifest = RunManifest(
                run_id=run_id,
                run_type="delivery",
                status=RunStatus.RUNNING,
                settings_digest=self.settings.digest,
                relationship_vector_digest=learning_start.content_digest,
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
                relationship_vector_digest=learning_start.content_digest,
                input_refs=[
                    "build_request.json",
                    "frozen_relationship_vectors.json",
                    "inference_relationships.start.json",
                ],
            )
            store.append_event(
                event_type="delivery.intake.validated",
                status="completed",
                stage=Stage.INTAKE.value,
                settings_digest=self.settings.digest,
                relationship_vector_digest=learning_start.content_digest,
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
            learning_state=learning_start,
        )
        shadow_state = self._restore_shadow_state(store, learning_start)
        handoffs = self._load_handoffs(store)
        proposed_memories = {
            agent: list(lines) for agent, lines in learning_start.memories.items()
        }
        for handoff in handoffs:
            self._accumulate_memories(proposed_memories, handoff)
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
                    relationship_vector_digest=learning_start.content_digest,
                    settings_digest=self.settings.digest,
                )
                state["current_stage"] = stage.value
                store.write_state(state)
                store.append_event(
                    event_type="delivery.stage.started",
                    status="running",
                    stage=stage.value,
                    relationship_vector_digest=learning_start.content_digest,
                    settings_digest=self.settings.digest,
                )
                stage_index = DELIVERY_STAGES.index(stage)
                producer_stage = (
                    DELIVERY_STAGES[stage_index - 1] if stage_index > 0 else None
                )
                upstream_pair = (
                    (
                        artifacts[producer_stage].lead,
                        artifacts[producer_stage].peer,
                    )
                    if producer_stage is not None
                    else None
                )
                if stage == Stage.OBSERVE:
                    self._hydrate_execute_command_receipts(previous, store)
                pair_result = pair_runner.run(
                    stage=stage,
                    request=request,
                    previous=previous,
                    attempt=1,
                    upstream_pair=upstream_pair,
                )
                if producer_stage is not None:
                    existing_assessment = next(
                        (
                            handoff
                            for handoff in handoffs
                            if handoff.producer_stage == producer_stage
                            and handoff.consumer_stage == stage
                        ),
                        None,
                    )
                    if existing_assessment is not None:
                        # A resumed stage must not teach the same handoff twice. The first
                        # completed downstream assessment is the canonical reward.
                        continue_handoff_learning = False
                    else:
                        continue_handoff_learning = True
                    assessment = self._handoff_assessment(
                        run_id,
                        producer_stage,
                        stage,
                        artifacts[producer_stage],
                        pair_result,
                    )
                    if continue_handoff_learning and assessment.reward == 0:
                        previous_digest = self._repair_rejected_handoff(
                            store=store,
                            request=request,
                            previous=previous,
                            artifacts=artifacts,
                            producer_stage=producer_stage,
                            consumer_stage=stage,
                            assessment=assessment,
                            pair_runner=pair_runner,
                        )
                        assessment.revised = True
                        pair_result = pair_runner.run(
                            stage=stage,
                            request=request,
                            previous=previous,
                            attempt=2,
                            upstream_pair=upstream_pair,
                        )
                        if not all(pair_result.upstream_assessments.values()):
                            raise RuntimeError(
                                f"{stage.value} still rejects {producer_stage.value} after bounded repair"
                            )
                    if continue_handoff_learning:
                        handoffs.append(assessment)
                        store.append_log(
                            "handoff_rewards", assessment.model_dump(mode="json")
                        )
                        store.append_event(
                            event_type="delivery.handoff.rewarded",
                            status="completed",
                            stage=stage.value,
                            relationship_vector_digest=learning_start.content_digest,
                            payload={
                                "producer_stage": producer_stage.value,
                                "consumer_stage": stage.value,
                                "reward": assessment.reward,
                                "revised": assessment.revised,
                            },
                        )
                        updates = propose_handoff_updates(
                            shadow_state,
                            assessment,
                            producer_lead=artifacts[producer_stage].lead,
                            producer_peer=artifacts[producer_stage].peer,
                            alpha=float(self.settings.continual_learning["trust_alpha"]),
                            step_max=float(
                                self.settings.continual_learning["trust_step_max"]
                            ),
                            expectation_beta=float(
                                self.settings.continual_learning["expectation_beta"]
                            ),
                        )
                        for update in updates:
                            store.append_log(
                                "shadow_trust_updates", update.model_dump(mode="json")
                            )
                        self._accumulate_memories(proposed_memories, assessment)
                candidate = dict(pair_result.candidate)
                build_evidence: BuildEvidence | None = None
                build_review_findings = []
                if stage == Stage.EXECUTE:
                    build_evidence, build_review_findings = self._build_workspace(
                        store,
                        request,
                        candidate,
                        previous=previous,
                        pair_runner=pair_runner,
                        lead_name=pair_result.lead,
                        peer_name=pair_result.peer,
                    )
                    self._attach_build_evidence(candidate, build_evidence)
                if stage == Stage.OBSERVE:
                    self._attach_observe_evidence(candidate, previous)
                if fault_stage == stage:
                    missing = STAGE_SPECS[stage].required_fields[0]
                    candidate.pop(missing, None)
                initial_peer_findings = self._post_workspace_peer_findings(
                    stage, pair_result.peer_findings
                )
                gate = self.gates.evaluate(
                    stage,
                    candidate,
                    [*initial_peer_findings, *build_review_findings],
                    expected_contract_ids=self._expected_contract_ids(stage, previous),
                    valid_evidence_refs=(
                        self._observe_evidence_refs(previous)
                        if stage == Stage.OBSERVE
                        else None
                    ),
                )
                cumulative_repair_findings = self._historical_repair_findings(
                    store, stage
                )
                if cumulative_repair_findings:
                    cumulative_repair_findings = self._merge_blocking_findings(
                        cumulative_repair_findings, gate.findings
                    )
                    gate = self._audit_cumulative_repair_resolution(
                        gate,
                        cumulative_repair_findings,
                        review_performed=(
                            stage != Stage.EXECUTE
                            or bool(build_evidence and build_evidence.passed)
                        ),
                    )
                store.append_log("gates", gate.model_dump(mode="json"))
                version = self._next_artifact_version(store, stage)
                repair_budget = self._repair_budget(stage)
                repairs_used = self._repairs_used(store, stage)
                manifest.repair_counts[stage.value] = repairs_used
                while gate.decision == GateDecision.REPAIR:
                    cumulative_repair_findings = self._merge_blocking_findings(
                        cumulative_repair_findings, gate.findings
                    )
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
                        relationship_vector_digest=learning_start.content_digest,
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
                        findings=cumulative_repair_findings,
                        artifact_refs=failed_envelope.content_files,
                        expected_condition=(
                            "All deterministic gate rules pass and every cumulative "
                            "critical/high finding is explicitly re-reviewed as resolved."
                        ),
                    )
                    store.append_log("repairs", packet.model_dump(mode="json"))
                    candidate = pair_runner.repair(
                        packet=packet,
                        request=request,
                        previous=previous,
                        lead_name=pair_result.lead,
                    )
                    cumulative_review_performed = False
                    if stage == Stage.EXECUTE:
                        build_evidence, build_review_findings = self._verify_build_workspace(
                            store,
                            request,
                            candidate,
                            previous=previous,
                            pair_runner=pair_runner,
                            lead_name=pair_result.lead,
                            peer_name=pair_result.peer,
                            attempt=packet.attempt,
                        )
                        self._attach_build_evidence(candidate, build_evidence)
                        if build_evidence.passed:
                            cumulative_review_findings = (
                                pair_runner.review_repaired_candidate(
                                    packet=packet,
                                    request=request,
                                    previous=previous,
                                    candidate=candidate,
                                    lead_name=pair_result.lead,
                                )
                            )
                            build_review_findings = [
                                *build_review_findings,
                                *cumulative_review_findings,
                            ]
                            cumulative_review_performed = True
                    else:
                        build_review_findings = pair_runner.review_repaired_candidate(
                            packet=packet,
                            request=request,
                            previous=previous,
                            candidate=candidate,
                            lead_name=pair_result.lead,
                        )
                        cumulative_review_performed = True
                    if stage == Stage.OBSERVE:
                        self._attach_observe_evidence(candidate, previous)
                    gate = self.gates.evaluate(
                        stage,
                        candidate,
                        build_review_findings,
                        expected_contract_ids=self._expected_contract_ids(
                            stage, previous
                        ),
                        valid_evidence_refs=(
                            self._observe_evidence_refs(previous)
                            if stage == Stage.OBSERVE
                            else None
                        ),
                    )
                    cumulative_repair_findings = self._merge_blocking_findings(
                        cumulative_repair_findings, gate.findings
                    )
                    gate = self._audit_cumulative_repair_resolution(
                        gate,
                        cumulative_repair_findings,
                        review_performed=cumulative_review_performed,
                    )
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
                    relationship_vector_digest=learning_start.content_digest,
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
                if self.adapter.name == "codex" and (
                    demo.capture_mode != "browser-interactive"
                    or demo.mutating_actions_completed < 2
                    or demo.unique_state_count < 2
                    or not is_presenter_quality_narration(demo.narration_provider)
                ):
                    raise RuntimeError(
                        "cannot resume past a DEMO artifact without verified interactions"
                    )
            else:
                demo = self.demo_assembler.assemble(
                    store=store,
                    preview_html=store.workspace_dir / "app" / "dist" / "index.html",
                    demo_plan=observe["demo_plan"],
                    narration_script=observe["narration_script"],
                    max_seconds=int(self.settings.delivery["max_demo_seconds"]),
                    require_interactive=self.adapter.name == "codex",
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
                            "visual_content": demo.visual_content,
                            "has_motion": demo.has_motion,
                            "capture_mode": demo.capture_mode,
                            "narration_provider": demo.narration_provider,
                            "capture_report": (
                                str(demo.capture_report_path.relative_to(store.root))
                                if demo.capture_report_path
                                else None
                            ),
                            "mutating_actions_completed": demo.mutating_actions_completed,
                            "unique_state_count": demo.unique_state_count,
                            "checksum": demo.checksum,
                        }
                    },
                    input_digests={
                        "observe": artifacts[Stage.OBSERVE].content_digest,
                        "demo_plan": digest_value(observe["demo_plan"]),
                        "narration": digest_value(observe["narration_script"]),
                    },
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
                            "demo.visual_content": demo.visual_content,
                            "demo.motion_present": demo.has_motion,
                            "demo.interactive_browser_capture": (
                                demo.capture_mode == "browser-interactive"
                            ),
                            "demo.presenter_quality_narration": (
                                is_presenter_quality_narration(
                                    demo.narration_provider
                                )
                                if self.adapter.name == "codex"
                                else True
                            ),
                            "demo.mutating_actions_completed": (
                                demo.mutating_actions_completed >= 2
                                if self.adapter.name == "codex"
                                else True
                            ),
                            "demo.visible_state_changed": (
                                demo.unique_state_count >= 2
                                if self.adapter.name == "codex"
                                else True
                            ),
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
            self._verify_frozen_snapshot(frozen, Stage.BUNDLE)
            shadow_rows = self._canonical_shadow_rows(store)
            handoff_rows = self._canonical_handoff_rows(store)
            store.atomic_jsonl(
                "logs/handoff_rewards.canonical.jsonl",
                handoff_rows,
            )
            store.atomic_jsonl(
                "logs/shadow_trust_updates.canonical.jsonl",
                shadow_rows,
            )
            if not learning_already_committed:
                night = pair_runner.run_inference_night_workspace(
                    shadow_updates=shadow_rows,
                    handoffs=handoff_rows,
                    proposed_memory_updates=proposed_memories,
                )
                store.append_log("inference_night_commits", night.model_dump(mode="json"))
                committed_learning = self.learning_store.commit(
                    run_id=run_id,
                    start_state=learning_start,
                    shadow_state=shadow_state,
                    night=night,
                )
                delta_payload = {
                    edge_id: {
                        "baseline": committed_learning.baseline_trust[edge_id],
                        "start": learning_start.vectors[edge_id].vector.trust,
                        "end": edge.vector.trust,
                        "delta_this_run": (
                            edge.vector.trust
                            - learning_start.vectors[edge_id].vector.trust
                        ),
                        "delta_from_training_baseline": (
                            committed_learning.trust_deltas_from_baseline[edge_id]
                        ),
                    }
                    for edge_id, edge in committed_learning.vectors.items()
                    if edge.vector.trust
                    != learning_start.vectors[edge_id].vector.trust
                }
                store.atomic_json("inference_relationships.end.json", committed_learning)
                store.atomic_json("inference_relationship_deltas.json", delta_payload)
                store.atomic_json(
                    "inference_night_commit.json",
                    {
                        "run_id": run_id,
                        "start_digest": learning_start.content_digest,
                        "end_digest": committed_learning.content_digest,
                        "commit_sequence": committed_learning.commit_sequence,
                        "night_workspace": night.model_dump(mode="json"),
                    },
                )
                store.append_event(
                    event_type="delivery.inference_learning.committed",
                    status="completed",
                    stage="NIGHT_COMMIT",
                    actor="Inference Night Curator",
                    adapter=self.adapter.name,
                    model=self.settings.models["night"]["model"],
                    reasoning_effort=self.settings.models["night"]["reasoning_effort"],
                    relationship_vector_digest=learning_start.content_digest,
                    output_refs=[
                        "inference_relationships.end.json",
                        "inference_relationship_deltas.json",
                        "inference_night_commit.json",
                    ],
                    payload={
                        "end_relationship_vector_digest": committed_learning.content_digest,
                        "commit_sequence": committed_learning.commit_sequence,
                        "shadow_update_count": len(shadow_rows),
                        "handoff_reward_count": len(handoff_rows),
                    },
                )
            assert committed_learning is not None
            state["learning_commit_sequence"] = committed_learning.commit_sequence
            state["relationship_vector_digest"] = committed_learning.content_digest
            store.write_state(state)
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
                relationship_vector_digest=learning_start.content_digest,
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
                relationship_vector_digest=learning_start.content_digest,
                error={"code": type(exc).__name__, "message": str(exc)},
            )
            raise

    def _handoff_assessment(
        self,
        run_id: str,
        producer_stage: Stage,
        consumer_stage: Stage,
        producer: ArtifactEnvelope,
        result: PairResult,
    ) -> HandoffAssessment:
        reward = int(bool(result.upstream_assessments) and all(
            result.upstream_assessments.values()
        ))
        return HandoffAssessment(
            run_id=run_id,
            producer_stage=producer_stage,
            consumer_stage=consumer_stage,
            producer_agents=[producer.lead, producer.peer],
            consumer_agents=list(result.upstream_assessments),
            assessments=result.upstream_assessments,
            revision_requests=result.revision_requests,
            reward=reward,
            evidence=[*producer.content_files],
        )

    def _repair_rejected_handoff(
        self,
        *,
        store: RunStore,
        request: BuildRequest,
        previous: dict[Stage, dict[str, Any]],
        artifacts: dict[Stage, ArtifactEnvelope],
        producer_stage: Stage,
        consumer_stage: Stage,
        assessment: HandoffAssessment,
        pair_runner: PairRunner,
    ) -> str:
        if int(self.settings.continual_learning["cross_stage_repair_budget"]) < 1:
            raise RuntimeError(
                f"{consumer_stage.value} rejected {producer_stage.value}, but cross-stage repair is disabled"
            )
        requests = [
            message
            for messages in assessment.revision_requests.values()
            for message in messages
        ] or [f"{consumer_stage.value} rejected the upstream artifact."]
        findings = [
            ReviewFinding(
                rule_id=f"handoff.{consumer_stage.value.lower()}.revision.{index}",
                severity="high",
                message=message,
                evidence=artifacts[producer_stage].content_files,
            )
            for index, message in enumerate(requests, 1)
        ]
        packet = RepairPacket(
            repair_id=f"handoff_repair_{uuid.uuid4().hex[:10]}",
            run_id=store.run_id,
            stage=producer_stage,
            attempt=2,
            remaining_budget=0,
            findings=findings,
            artifact_refs=artifacts[producer_stage].content_files,
            expected_condition=f"The {consumer_stage.value} pair accepts the revised output.",
        )
        store.append_log("repairs", packet.model_dump(mode="json"))
        upstream_previous = {
            stage: artifact for stage, artifact in previous.items() if stage != producer_stage
        }
        candidate = pair_runner.repair(
            packet=packet,
            request=request,
            previous=upstream_previous,
            lead_name=artifacts[producer_stage].lead,
        )
        build_findings: list[Any] = []
        cumulative_review_performed = False
        if producer_stage == Stage.EXECUTE:
            build_evidence, build_findings = self._verify_build_workspace(
                store,
                request,
                candidate,
                previous=upstream_previous,
                pair_runner=pair_runner,
                lead_name=artifacts[producer_stage].lead,
                peer_name=artifacts[producer_stage].peer,
                attempt=2,
            )
            self._attach_build_evidence(candidate, build_evidence)
            if build_evidence.passed:
                build_findings = [
                    *build_findings,
                    *pair_runner.review_repaired_candidate(
                        packet=packet,
                        request=request,
                        previous=upstream_previous,
                        candidate=candidate,
                        lead_name=artifacts[producer_stage].lead,
                    ),
                ]
                cumulative_review_performed = True
        else:
            build_findings = pair_runner.review_repaired_candidate(
                packet=packet,
                request=request,
                previous=upstream_previous,
                candidate=candidate,
                lead_name=artifacts[producer_stage].lead,
            )
            cumulative_review_performed = True
        if producer_stage == Stage.OBSERVE:
            self._attach_observe_evidence(candidate, upstream_previous)
        gate = self.gates.evaluate(
            producer_stage,
            candidate,
            build_findings,
            expected_contract_ids=self._expected_contract_ids(
                producer_stage, upstream_previous
            ),
            valid_evidence_refs=(
                self._observe_evidence_refs(upstream_previous)
                if producer_stage == Stage.OBSERVE
                else None
            ),
        )
        cumulative_findings = self._merge_blocking_findings(
            findings, gate.findings
        )
        gate = self._audit_cumulative_repair_resolution(
            gate,
            cumulative_findings,
            review_performed=cumulative_review_performed,
        )
        store.append_log("gates", gate.model_dump(mode="json"))
        if gate.decision != GateDecision.PASS:
            raise RuntimeError(
                f"cross-stage repair failed deterministic gates for {producer_stage.value}"
            )
        old = artifacts[producer_stage]
        envelope = self._commit_stage_artifact(
            store,
            producer_stage,
            self._next_artifact_version(store, producer_stage),
            candidate,
            old.lead,
            old.peer,
            old.content_digest,
            GateDecision.PASS,
        )
        previous[producer_stage] = candidate
        artifacts[producer_stage] = envelope
        store.append_event(
            event_type="delivery.handoff.revised",
            status="completed",
            stage=producer_stage.value,
            attempt=2,
            output_refs=envelope.content_files,
            payload={
                "requested_by": consumer_stage.value,
                "original_artifact_digest": old.content_digest,
                "revised_artifact_digest": envelope.content_digest,
            },
        )
        return envelope.content_digest

    def _restore_shadow_state(
        self,
        store: RunStore,
        start: InferenceLearningState,
    ) -> InferenceLearningState:
        shadow = start.model_copy(deep=True)
        beta = float(self.settings.continual_learning["expectation_beta"])
        for row in self._canonical_shadow_rows(store):
            edge_id = f"{row['source']}->{row['target']}"
            shadow.vectors[edge_id].vector.trust = float(row["shadow_trust_after"])
            before = float(row["expected_success_before"])
            rpe = float(row["reward_prediction_error"])
            shadow.expected_success[edge_id] = max(0.0, min(1.0, before + beta * rpe))
            shadow.trust_deltas_from_baseline[edge_id] = (
                shadow.vectors[edge_id].vector.trust - shadow.baseline_trust[edge_id]
            )
        return sign_state(shadow)

    @staticmethod
    def _load_handoffs(store: RunStore) -> list[HandoffAssessment]:
        handoffs: list[HandoffAssessment] = []
        for row in DeliveryOrchestrator._canonical_handoff_rows(store):
            payload = {key: value for key, value in row.items() if key not in {"phase", "timestamp"}}
            handoffs.append(HandoffAssessment.model_validate(payload))
        return handoffs

    @staticmethod
    def _canonical_handoff_rows(store: RunStore) -> list[dict[str, Any]]:
        canonical: dict[tuple[str, str], dict[str, Any]] = {}
        for row in RunStore.read_jsonl(store.logs_dir / "handoff_rewards.jsonl"):
            key = (str(row["producer_stage"]), str(row["consumer_stage"]))
            canonical.setdefault(key, row)
        return list(canonical.values())

    @staticmethod
    def _canonical_shadow_rows(store: RunStore) -> list[dict[str, Any]]:
        canonical: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in RunStore.read_jsonl(
            store.logs_dir / "shadow_trust_updates.jsonl"
        ):
            key = (
                str(row["producer_stage"]),
                str(row["consumer_stage"]),
                str(row["source"]),
                str(row["target"]),
            )
            canonical.setdefault(key, row)
        return list(canonical.values())

    @staticmethod
    def _accumulate_memories(
        memories: dict[str, list[str]],
        handoff: HandoffAssessment,
    ) -> None:
        evidence_ref = handoff.evidence[0] if handoff.evidence else "handoff-log"
        evidence_tag = f"[run:{handoff.run_id}][evidence:{evidence_ref}]"
        outcome = "accepted" if handoff.reward else "requested a revision to"
        for consumer in handoff.consumer_agents:
            detail = "; ".join(handoff.revision_requests.get(consumer, []))
            line = (
                f"{evidence_tag} During {handoff.consumer_stage.value}, I {outcome} "
                f"{handoff.producer_stage.value}."
            )
            if detail:
                line = f"{line} Reason: {detail}"
            memories[consumer] = [*memories.get(consumer, []), line][-5:]
        producer_line = (
            f"{evidence_tag} {handoff.consumer_stage.value} gave our "
            f"{handoff.producer_stage.value} "
            f"handoff reward {handoff.reward}."
        )
        for producer in handoff.producer_agents:
            memories[producer] = [*memories.get(producer, []), producer_line][-5:]

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
            manifest.stage_outcomes[stage.value] = "PASS"
            manifest.artifact_checksums[stage.value] = envelope.content_digest
        if Stage.DEMO.value in completed:
            envelope = best.get(Stage.DEMO)
            if envelope is None or not store.verify_artifact(envelope):
                raise ValueError("resume boundary has an invalid DEMO artifact")
            manifest.stage_outcomes[Stage.DEMO.value] = "PASS"
            manifest.artifact_checksums[Stage.DEMO.value] = envelope.content_digest
        manifest.repair_counts = {
            stage.value: self._repairs_used(store, stage)
            for stage in DELIVERY_STAGES
            if self._repairs_used(store, stage)
        }
        return state, manifest, best, previous, previous_digest

    def _reconcile_resume_workspace_drift(
        self,
        *,
        store: RunStore,
        state: dict[str, Any],
        manifest: RunManifest,
        artifacts: dict[Stage, ArtifactEnvelope],
        previous: dict[Stage, dict[str, Any]],
        previous_digest: str,
    ) -> str:
        """Invalidate EXECUTE and downstream state when its live workspace drifted.

        A rejected downstream handoff may legitimately ask EXECUTE to repair the app.
        If that repair then fails its deterministic gates, the mutable workspace can be
        newer than the last immutable PASS artifact. Resuming from OBSERVE in that state
        would silently bless uncommitted code. Roll the durable boundary back to EXECUTE
        so the current workspace must pass implementation review, commands, and gates and
        be committed as a new immutable EXECUTE artifact before it can be consumed.
        """

        completed = list(state.get("completed_stages", []))
        if Stage.EXECUTE.value not in completed:
            return previous_digest
        execute = previous.get(Stage.EXECUTE, {})
        expected = execute.get("build_evidence", {}).get("file_digests")
        if not isinstance(expected, dict) or not expected:
            # Older/fixture artifacts without a workspace digest cannot prove drift and
            # retain their established resume behavior.
            return previous_digest
        app_dir = store.workspace_dir / "app"
        actual = self._workspace_file_digests(app_dir)
        if actual == expected:
            return previous_digest

        invalidated = {
            Stage.EXECUTE.value,
            Stage.OBSERVE.value,
            Stage.DEMO.value,
            Stage.BUNDLE.value,
        }
        state["completed_stages"] = [
            stage for stage in completed if stage not in invalidated
        ]
        state["current_stage"] = Stage.EXECUTE.value
        for stage in (Stage.EXECUTE, Stage.OBSERVE, Stage.DEMO, Stage.BUNDLE):
            artifacts.pop(stage, None)
            previous.pop(stage, None)
            manifest.stage_outcomes.pop(stage.value, None)
            manifest.artifact_checksums.pop(stage.value, None)
        prior = artifacts.get(Stage.SIMULATE)
        if prior is None:
            raise ValueError("workspace drift rollback has no committed SIMULATE boundary")
        state["status"] = RunStatus.REPAIRING.value
        state.pop("failure_reason", None)
        store.write_state(state)
        store.write_manifest(manifest)
        store.append_event(
            event_type="delivery.resume.workspace_drift_detected",
            status="repairing",
            stage=Stage.EXECUTE.value,
            settings_digest=self.settings.digest,
            relationship_vector_digest=state.get("relationship_vector_digest"),
            payload={
                "invalidated_stages": sorted(invalidated),
                "expected_workspace_digest": digest_value(expected),
                "actual_workspace_digest": digest_value(actual),
                "expected_files": len(expected),
                "actual_files": len(actual),
            },
        )
        return prior.content_digest

    @staticmethod
    def _workspace_file_digests(workspace: Path) -> dict[str, str]:
        if not workspace.is_dir():
            return {}
        return {
            str(path.relative_to(workspace)): digest_file(path)
            for path in sorted(workspace.rglob("*"))
            if path.is_file() and "node_modules" not in path.parts
        }

    @staticmethod
    def _next_artifact_version(store: RunStore, stage: Stage) -> int:
        versions = [
            int(row["version"])
            for row in RunStore.read_jsonl(store.logs_dir / "artifacts.jsonl")
            if row.get("stage") == stage.value
        ]
        return max(versions, default=0) + 1

    @staticmethod
    def _repairs_used(store: RunStore, stage: Stage) -> int:
        epoch_started_at: datetime | None = None
        for event in RunStore.read_jsonl(store.root / "events.jsonl"):
            if event.get("event_type") != "delivery.resume.workspace_drift_detected":
                continue
            invalidated = event.get("payload", {}).get("invalidated_stages", [])
            if stage.value not in invalidated:
                continue
            timestamp = DeliveryOrchestrator._parse_log_timestamp(
                event.get("timestamp")
            )
            if timestamp is not None:
                epoch_started_at = timestamp

        repair_ids: set[str] = set()
        for row in RunStore.read_jsonl(store.logs_dir / "repairs.jsonl"):
            if row.get("stage") != stage.value or not row.get("repair_id"):
                continue
            repair_id = str(row["repair_id"])
            if repair_id.startswith("handoff_repair_"):
                # Cross-stage revisions have their own budget and must not consume the
                # repaired stage's independent deterministic-gate budget.
                continue
            if epoch_started_at is not None:
                repaired_at = DeliveryOrchestrator._parse_log_timestamp(
                    row.get("timestamp")
                )
                if repaired_at is None or repaired_at <= epoch_started_at:
                    continue
            repair_ids.add(repair_id)
        return len(repair_ids)

    @staticmethod
    def _parse_log_timestamp(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _post_workspace_peer_findings(
        stage: Stage, findings: list[ReviewFinding]
    ) -> list[ReviewFinding]:
        """Discard pre-build runtime claims once deterministic EXECUTE owns them.

        The EXECUTE pair first reviews an autonomous-change plan whose build evidence is
        intentionally empty. Runtime commands, package policy, browser receipts, and their
        exact failures are established moments later by the command runner plus the
        independent workspace reviewer. Carrying the plan review's speculative runtime
        findings into that post-build gate would make fresh passing evidence impossible to
        clear. Contract and implementation-plan findings remain intact.
        """

        if stage != Stage.EXECUTE:
            return findings
        runtime_prefixes = (
            "execute.commands_verified",
            "execute.workspace.",
            "execute.command.",
        )
        return [
            finding
            for finding in findings
            if not finding.rule_id.startswith(runtime_prefixes)
        ]

    @classmethod
    def _historical_repair_findings(
        cls, store: RunStore, stage: Stage
    ) -> list[ReviewFinding]:
        """Recover cumulative blocking findings after an interrupted stage.

        Gate and repair logs are append-only evidence. Both are required because the
        terminal gate that exhausts the budget never receives a following RepairPacket.
        Rehydrating their critical/high findings prevents a resumed stage from silently
        forgetting an earlier defect before the packet-aware peer has re-reviewed it.
        """

        findings: list[ReviewFinding] = []
        sources = (
            (
                RunStore.read_jsonl(store.logs_dir / "gates.jsonl"),
                lambda row: (
                    row.get("stage") == stage.value
                    and row.get("decision") == GateDecision.REPAIR.value
                ),
            ),
            (
                RunStore.read_jsonl(store.logs_dir / "repairs.jsonl"),
                lambda row: row.get("stage") == stage.value,
            ),
        )
        for rows, belongs_to_stage in sources:
            for row in rows:
                if not belongs_to_stage(row):
                    continue
                for finding in row.get("findings", []):
                    if not isinstance(finding, dict):
                        continue
                    try:
                        findings.append(ReviewFinding.model_validate(finding))
                    except (TypeError, ValueError):
                        continue
        return cls._merge_blocking_findings([], findings)

    @staticmethod
    def _merge_blocking_findings(
        retained: list[ReviewFinding], current: list[ReviewFinding]
    ) -> list[ReviewFinding]:
        """Keep one stable cumulative record for every blocking repair condition."""

        severity_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        merged: dict[str, ReviewFinding] = {}
        for finding in [*retained, *current]:
            if finding.severity not in {"critical", "high"}:
                continue
            prior = merged.get(finding.rule_id)
            if prior is None:
                merged[finding.rule_id] = finding
                continue
            severity = (
                finding.severity
                if severity_rank[finding.severity] > severity_rank[prior.severity]
                else prior.severity
            )
            merged[finding.rule_id] = ReviewFinding(
                rule_id=prior.rule_id,
                severity=severity,
                message=prior.message,
                evidence=list(dict.fromkeys([*prior.evidence, *finding.evidence])),
            )
        return list(merged.values())

    @staticmethod
    def _audit_cumulative_repair_resolution(
        gate: GateResult,
        cumulative_findings: list[ReviewFinding],
        *,
        review_performed: bool,
    ) -> GateResult:
        """Fail closed until every retained condition has a resolution receipt.

        Deterministic findings resolve only when their named rule is now true. Semantic
        findings resolve when a completed peer re-review received the cumulative packet
        and no longer reports that rule. Resolution receipts are embedded in the gate log
        so PASS is independently auditable rather than inferred from a missing finding.
        """

        rule_results = dict(gate.rule_results)
        findings = list(gate.findings)
        evidence = list(gate.evidence)
        blocking_now = {
            finding.rule_id
            for finding in findings
            if finding.severity in {"critical", "high"}
        }
        all_resolved = True
        for finding in cumulative_findings:
            deterministic_result = gate.rule_results.get(finding.rule_id)
            if deterministic_result is not None:
                resolved = bool(deterministic_result)
                source = "deterministic-pass" if resolved else "deterministic-fail"
            else:
                resolved = review_performed and finding.rule_id not in blocking_now
                source = (
                    "peer-review-cleared"
                    if resolved
                    else "peer-review-unresolved"
                    if review_performed
                    else "peer-review-missing"
                )
            rule_results[f"repair.cumulative.{finding.rule_id}"] = resolved
            evidence.append(
                f"repair-resolution:{finding.rule_id}:{source}"
            )
            all_resolved = all_resolved and resolved

        if gate.decision == GateDecision.PASS and not review_performed:
            rule_id = f"{gate.stage.value.lower()}.repair.cumulative_review_required"
            rule_results[rule_id] = False
            findings.append(
                ReviewFinding(
                    rule_id=rule_id,
                    severity="critical",
                    message=(
                        "A packet-aware peer re-review must verify every cumulative "
                        "repair finding before this stage can pass."
                    ),
                    evidence=[
                        finding.rule_id for finding in cumulative_findings
                    ],
                )
            )
            evidence.append("repair-resolution:cumulative-review:missing")
            all_resolved = False

        return GateResult(
            stage=gate.stage,
            decision=(
                GateDecision.PASS
                if gate.decision == GateDecision.PASS and all_resolved
                else GateDecision.REPAIR
            ),
            rule_results=rule_results,
            findings=findings,
            evidence=evidence,
        )

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
            visual_content=bool(payload.get("visual_content", False)),
            has_motion=bool(payload.get("has_motion", False)),
            capture_mode=str(payload.get("capture_mode", "unknown")),
            narration_provider=str(payload.get("narration_provider", "unknown")),
            checksum=str(payload["checksum"]),
            capture_report_path=(
                store.root / payload["capture_report"]
                if payload.get("capture_report")
                else None
            ),
            mutating_actions_completed=int(
                payload.get("mutating_actions_completed", 0)
            ),
            unique_state_count=int(payload.get("unique_state_count", 0)),
        )

    def _build_workspace(
        self,
        store: RunStore,
        request: BuildRequest,
        candidate: dict[str, Any],
        *,
        previous: dict[Stage, dict[str, Any]],
        pair_runner: PairRunner,
        lead_name: str,
        peer_name: str,
    ) -> tuple[BuildEvidence, list[Any]]:
        app_dir = store.workspace_dir / "app"
        command_snapshot = store.workspace_dir / "build_commands.snapshot.json"
        if app_dir.exists() and command_snapshot.exists():
            commands = json.loads(command_snapshot.read_text())
            evidence = self.workspace_builder.inspect(
                app_dir,
                candidate,
                commands=commands,
            )
        else:
            if app_dir.exists():
                shutil.rmtree(app_dir)
            evidence = self.workspace_builder.build(app_dir, request, candidate)
            store.atomic_json("workspace/build_commands.snapshot.json", evidence.commands)
        implementer_report = pair_runner.implement_workspace(
            workspace=app_dir,
            request=request,
            candidate=candidate,
            previous=previous,
            lead_name=lead_name,
        )
        candidate["implementer_report"] = implementer_report
        return self._verify_build_workspace(
            store,
            request,
            candidate,
            previous=previous,
            pair_runner=pair_runner,
            lead_name=lead_name,
            peer_name=peer_name,
            attempt=1,
        )

    def _verify_build_workspace(
        self,
        store: RunStore,
        request: BuildRequest,
        candidate: dict[str, Any],
        *,
        previous: dict[Stage, dict[str, Any]],
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
        evidence = self._enforce_browser_acceptance_requirement(evidence)
        preflight_rules = (
            "react_entry_exists",
            "vite_config_exists",
            "package_is_private",
            "command_manifest_unchanged",
            "no_external_services",
            "dependencies_exactly_pinned",
            "node_module_links_local",
            "package_scripts_allowlisted",
            "test_harnesses_declared",
        ) + (
            ("browser_acceptance_harness_present",)
            if self.adapter.name == "codex"
            else ()
        )
        if not all(evidence.checks.get(rule, False) for rule in preflight_rules):
            return evidence, self._workspace_evidence_findings(
                evidence, check_names=preflight_rules
            )
        commands = execute_validation_commands(
            evidence.commands,
            fixture=self.adapter.name == "fixture",
        )
        if self.adapter.name != "fixture":
            # Invalidate success from every earlier attempt before any operation in
            # this validation transaction can fail. The final npm-test journey is the
            # only command allowed to recreate the receipt.
            (
                app_dir / "artifacts" / "browser-acceptance-receipt.json"
            ).unlink(missing_ok=True)
        command_results = CommandRunner(
            workspace=app_dir,
            allowed_commands=set(commands),
            store=store,
        ).run_many(commands, timeout_seconds=int(self.settings.training["timeout_seconds"]))
        compact_command_results = tuple(result.__dict__ for result in command_results)
        inspect_command_results = (
            self._hydrate_failed_command_details(store, compact_command_results)
            if any(not result.passed for result in command_results)
            else compact_command_results
        )
        evidence = self.workspace_builder.inspect(
            app_dir,
            candidate,
            commands=evidence.commands,
            command_results=inspect_command_results,
        )
        evidence = self._enforce_browser_acceptance_requirement(evidence)
        if not evidence.passed:
            return evidence, self._workspace_evidence_findings(evidence)
        review_candidate = dict(candidate)
        self._attach_build_evidence(review_candidate, evidence)
        findings = pair_runner.review_workspace(
            workspace=app_dir,
            request=request,
            previous=previous,
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

    def _enforce_browser_acceptance_requirement(
        self, evidence: BuildEvidence
    ) -> BuildEvidence:
        """Require every live generated app to prove a real browser journey.

        Fixture runs keep their lightweight deterministic profile. Codex delivery runs
        must declare an acceptance harness so an otherwise healthy unit suite cannot be
        mistaken for end-to-end production-browser evidence.
        """

        if self.adapter.name != "codex":
            return evidence
        checks = dict(evidence.checks)
        checks["browser_acceptance_harness_present"] = any(
            Path(relative).name.lower().find("acceptance") >= 0
            and Path(relative).parts[:1] == ("tests",)
            for relative in evidence.files
        )
        return BuildEvidence(
            profile=evidence.profile,
            files=evidence.files,
            commands=evidence.commands,
            checks=checks,
            file_digests=evidence.file_digests,
            command_results=evidence.command_results,
        )

    @staticmethod
    def _hydrate_failed_command_details(
        store: RunStore, command_results: tuple[dict[str, Any], ...]
    ) -> tuple[dict[str, Any], ...]:
        """Attach bounded output excerpts so an EXECUTE repair can fix the failure.

        Successful immutable build evidence stays compact. When a command fails, its
        canonical append-only receipt is joined by command and output digests before
        the gate creates the repair packet. This gives the repair agent the actual
        compiler or test failure without weakening the deterministic gate.
        """

        logged = store.read_jsonl(store.logs_dir / "commands.jsonl")
        hydrated: list[dict[str, Any]] = []
        for compact in command_results:
            match = next(
                (
                    row
                    for row in reversed(logged)
                    if row.get("command") == compact.get("command")
                    and row.get("stdout_digest") == compact.get("stdout_digest")
                    and row.get("stderr_digest") == compact.get("stderr_digest")
                ),
                None,
            )
            merged = dict(compact)
            if match is not None:
                for key in ("stdout_tail", "stderr_tail"):
                    tail = str(match.get(key, ""))
                    if tail:
                        merged[key] = tail[-2000:]
            hydrated.append(merged)
        return tuple(hydrated)

    @staticmethod
    def _workspace_evidence_findings(
        evidence: BuildEvidence, *, check_names: tuple[str, ...] | None = None
    ) -> list[ReviewFinding]:
        """Convert deterministic workspace failures into bounded EXECUTE repairs."""

        selected = set(check_names or evidence.checks)
        findings = [
            ReviewFinding(
                rule_id=f"execute.workspace.{name}",
                severity=(
                    "critical"
                    if name
                    in {
                        "command_manifest_unchanged",
                        "no_external_services",
                        "node_module_links_local",
                    }
                    else "high"
                ),
                message=(
                    "Generated workspace package scripts are incomplete or outside the "
                    "allowlist. Preserve all three exact validated entries: test must be "
                    "`vitest run` or `vitest run && bash tests/browser.acceptance.sh`; "
                    "typecheck must be `tsc -b --pretty false`, `tsc -b`, or `tsc --noEmit`; "
                    "build must be `tsc -b && vite build` or `vite build`."
                    if name == "package_scripts_allowlisted"
                    else f"Generated workspace check failed: {name}."
                ),
                evidence=(
                    [
                        "required_script=test",
                        "required_script=typecheck",
                        "required_script=build",
                        f"build_evidence.checks.{name}=false",
                    ]
                    if name == "package_scripts_allowlisted"
                    else [f"build_evidence.checks.{name}=false"]
                ),
            )
            for name, passed in evidence.checks.items()
            if name in selected and not passed
        ]
        for index, result in enumerate(evidence.command_results, 1):
            if (
                result.get("exit_code") == 0
                and not result.get("timed_out")
                and not result.get("reported_error")
            ):
                continue
            command = str(result.get("command", f"command-{index}"))
            evidence_lines = [
                f"command={command}",
                f"exit_code={result.get('exit_code')}",
                f"timed_out={bool(result.get('timed_out'))}",
            ]
            if result.get("reported_error"):
                evidence_lines.append(
                    f"reported_error={result.get('reported_error')}"
                )
            for key in ("stdout_tail", "stderr_tail"):
                tail = str(result.get(key, "")).strip()
                if tail:
                    evidence_lines.append(f"{key}={tail[-1600:]}")
            findings.append(
                ReviewFinding(
                    rule_id=f"execute.command.{index}",
                    severity="high",
                    message=(
                        f"Deterministic workspace command failed and must be repaired: {command}."
                    ),
                    evidence=evidence_lines,
                )
            )
        if not findings:
            findings.append(
                ReviewFinding(
                    rule_id="execute.workspace.unresolved",
                    severity="high",
                    message="Generated workspace evidence did not pass all deterministic checks.",
                    evidence=["build_evidence.passed=false"],
                )
            )
        return findings

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

    @staticmethod
    def _expected_contract_ids(
        stage: Stage, previous: dict[Stage, dict[str, Any]]
    ) -> list[str] | None:
        if stage in {Stage.SENSE, Stage.MODEL} or Stage.MODEL not in previous:
            return None
        return contract_item_ids(previous[Stage.MODEL])

    @staticmethod
    def _observe_evidence_refs(
        previous: dict[Stage, dict[str, Any]]
    ) -> set[str]:
        execute = previous.get(Stage.EXECUTE, {})
        build = execute.get("build_evidence", {})
        refs = {
            f"logs/commands.jsonl#stdout_digest={row['stdout_digest']}"
            for row in build.get("command_results", [])
            if isinstance(row, dict)
            and row.get("exit_code") == 0
            and not row.get("timed_out")
            and not row.get("reported_error")
            and row.get("stdout_digest")
        }
        dist_digest = build.get("file_digests", {}).get("dist/index.html")
        if dist_digest:
            refs.add(f"workspace/app/dist/index.html#digest={dist_digest}")
        return refs

    @staticmethod
    def _hydrate_execute_command_receipts(
        previous: dict[Stage, dict[str, Any]], store: RunStore
    ) -> None:
        """Join append-only command receipts into the in-memory EXECUTE evidence.

        Immutable stage artifacts intentionally store compact command evidence. OBSERVE
        still needs the semantic browser receipt and the output digest from the canonical
        command log, especially after a resume. This join changes neither artifact bytes
        nor learning state.
        """

        execute = previous.get(Stage.EXECUTE)
        if not isinstance(execute, dict):
            return
        build = execute.get("build_evidence")
        if not isinstance(build, dict):
            return
        logged = store.read_jsonl(store.logs_dir / "commands.jsonl")
        hydrated: list[dict[str, Any]] = []
        for compact in build.get("command_results", []):
            if not isinstance(compact, dict):
                continue
            match = next(
                (
                    row
                    for row in reversed(logged)
                    if row.get("command") == compact.get("command")
                    and row.get("stdout_digest") == compact.get("stdout_digest")
                ),
                None,
            )
            merged = {**compact, **(match or {})}
            output_tail = f"{merged.get('stdout_tail', '')}\n{merged.get('stderr_tail', '')}"
            merged["browser_acceptance_receipt"] = bool(
                merged.get("browser_acceptance_receipt")
                or (
                    merged.get("exit_code") == 0
                    and not merged.get("timed_out")
                    and not merged.get("reported_error")
                    and "ZEROHANDOFF_BROWSER_ACCEPTANCE_OK" in output_tail
                )
            )
            hydrated.append(merged)
        build["command_results"] = hydrated
        build["browser_acceptance_receipt_verified"] = any(
            bool(row.get("browser_acceptance_receipt")) for row in hydrated
        )

    @classmethod
    def _attach_observe_evidence(
        cls,
        candidate: dict[str, Any],
        previous: dict[Stage, dict[str, Any]],
    ) -> None:
        refs = sorted(cls._observe_evidence_refs(previous))
        if not refs:
            return
        for entry in candidate.get("proof_entries", []):
            if isinstance(entry, dict):
                entry["evidence"] = refs

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
