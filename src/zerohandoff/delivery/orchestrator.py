from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zerohandoff.config import SettingsBundle, digest_value
from zerohandoff.continual import (
    InferenceLearningStore,
    propose_handoff_updates,
    sign_state,
    verify_inference_state,
)
from zerohandoff.delivery.bundle import BundleResult, DeliveryBundleAssembler
from zerohandoff.delivery.commands import CommandRunner
from zerohandoff.delivery.demo import DemoAssembler, DemoEvidence
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
                            "visual_content": demo.visual_content,
                            "capture_mode": demo.capture_mode,
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
                            "demo.visual_content": demo.visual_content,
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
        if producer_stage == Stage.EXECUTE:
            build_evidence, build_findings = self._verify_build_workspace(
                store,
                candidate,
                pair_runner=pair_runner,
                lead_name=artifacts[producer_stage].lead,
                peer_name=artifacts[producer_stage].peer,
                attempt=2,
            )
            self._attach_build_evidence(candidate, build_evidence)
        gate = self.gates.evaluate(producer_stage, candidate, build_findings)
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
        outcome = "accepted" if handoff.reward else "requested a revision to"
        for consumer in handoff.consumer_agents:
            detail = "; ".join(handoff.revision_requests.get(consumer, []))
            line = (
                f"During {handoff.consumer_stage.value}, I {outcome} "
                f"{handoff.producer_stage.value}."
            )
            if detail:
                line = f"{line} Reason: {detail}"
            memories[consumer] = [*memories.get(consumer, []), line][-5:]
        producer_line = (
            f"{handoff.consumer_stage.value} gave our {handoff.producer_stage.value} "
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
            visual_content=bool(payload.get("visual_content", False)),
            capture_mode=str(payload.get("capture_mode", "unknown")),
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
