from __future__ import annotations

import json

import pytest

from zerohandoff.config import digest_file, digest_value
from zerohandoff.delivery.gates import GateEngine
from zerohandoff.delivery.orchestrator import DeliveryOrchestrator, RunCancelled
from zerohandoff.delivery.pairs import inference_night_schema
from zerohandoff.models import (
    AgentResult,
    BuildRequest,
    GateDecision,
    GateResult,
    InvocationStatus,
    ReviewFinding,
    RunStatus,
    Stage,
)
from zerohandoff.runtime.fixture import FixtureAdapter
from zerohandoff.storage import RunStore
from zerohandoff.training.orchestrator import TrainingOrchestrator
from zerohandoff.training.puzzles import PuzzleRepository


def frozen_fixture(tmp_path, repo_root, settings):
    return TrainingOrchestrator(
        settings=settings,
        adapter=FixtureAdapter(),
        puzzle_repository=PuzzleRepository.load(repo_root / "data" / "puzzles.fixture.json"),
        base_dir=tmp_path / "training",
    ).run(rounds=1).frozen_snapshot


def test_scenario_model_gate_rejects_dependency_cycles() -> None:
    candidate = {
        "work_units": [{"id": "UNIT-001"}, {"id": "UNIT-002"}],
        "dependencies": [
            {"before": "UNIT-001", "after": "UNIT-002"},
            {"before": "UNIT-002", "after": "UNIT-001"},
        ],
        "execution_order": ["UNIT-001", "UNIT-002"],
        "contract_links": [
            {"contract_item_id": "CAP-001", "work_unit_id": "UNIT-001"}
        ],
    }
    result = GateEngine().evaluate(Stage.SIMULATE, candidate, [])
    assert result.decision == GateDecision.REPAIR
    assert result.rule_results["simulate.scenario_model_valid"] is False


@pytest.mark.parametrize("status", ["pass", "passed"])
def test_observe_gate_accepts_canonical_passing_statuses(status: str) -> None:
    candidate = {
        "proof_entries": [
            {
                "contract_item_id": "MODEL-01",
                "status": status,
                "evidence": ["recorded command evidence"],
            }
        ],
        "defects": [],
        "demo_plan": [{"step": 1}],
        "narration_script": "Show the verified workflow.",
    }
    result = GateEngine().evaluate(Stage.OBSERVE, candidate, [])
    assert result.rule_results["observe.must_have_evidenced"] is True
    assert result.decision == GateDecision.PASS


def test_inference_night_schema_requires_every_named_agent_memory() -> None:
    names = ["Aster", "Juno", "Peter"]
    schema = inference_night_schema(names)
    memories = schema["properties"]["memory_updates"]
    assert memories["additionalProperties"] is False
    assert memories["required"] == names
    assert set(memories["properties"]) == set(names)


def test_resume_learning_rows_are_canonicalized_by_handoff(tmp_path) -> None:
    store = RunStore(tmp_path, "resume_dedup", "delivery")
    handoff = {
        "producer_stage": "MODEL",
        "consumer_stage": "COMPOSE",
        "reward": 1,
    }
    shadow = {
        **handoff,
        "source": "Aster",
        "target": "Juno",
    }
    for _ in range(3):
        store.append_log("handoff_rewards", handoff)
        store.append_log("shadow_trust_updates", shadow)
    assert len(DeliveryOrchestrator._canonical_handoff_rows(store)) == 1
    assert len(DeliveryOrchestrator._canonical_shadow_rows(store)) == 1


def test_delivery_reaches_bundle_with_media_logs_and_valid_checksums(
    tmp_path, repo_root, settings, build_request
) -> None:
    frozen = frozen_fixture(tmp_path, repo_root, settings)
    assert frozen is not None
    adapter = FixtureAdapter()
    result = DeliveryOrchestrator(
        settings=settings, adapter=adapter, base_dir=tmp_path / "runs"
    ).run(request=build_request, frozen=frozen, run_id="delivery_complete")
    assert result.status == RunStatus.COMPLETED
    assert len(adapter.calls) == 31
    delivery_pair_calls = [call for call in adapter.calls if call.pair != "SYSTEM"]
    assert len(delivery_pair_calls) == 30
    assert all(call.relationship_policy is not None for call in delivery_pair_calls)
    night_call = next(call for call in adapter.calls if call.pair == "SYSTEM")
    assert night_call.inputs["purpose"] == "inference_night_commit"
    assert night_call.reasoning_effort == "xhigh"
    policies = RunStore.read_jsonl(
        result.store_root / "logs" / "relationship_policies.jsonl"
    )
    assert len(policies) == 30
    assert all(row["policy"]["source_vector_digest"] for row in policies)
    handoffs = RunStore.read_jsonl(result.store_root / "logs" / "handoff_rewards.jsonl")
    shadows = RunStore.read_jsonl(
        result.store_root / "logs" / "shadow_trust_updates.jsonl"
    )
    assert len(handoffs) == 6
    assert all(row["reward"] == 1 for row in handoffs)
    assert len(shadows) == 36
    assert len(
        RunStore.read_jsonl(
            result.store_root / "logs" / "handoff_rewards.canonical.jsonl"
        )
    ) == 6
    assert len(
        RunStore.read_jsonl(
            result.store_root / "logs" / "shadow_trust_updates.canonical.jsonl"
        )
    ) == 36
    start = json.loads((result.store_root / "inference_relationships.start.json").read_text())
    end = json.loads((result.store_root / "inference_relationships.end.json").read_text())
    assert start["content_digest"] != end["content_digest"]
    assert end["commit_sequence"] == start["commit_sequence"] + 1
    assert (result.store_root / "inference_relationship_deltas.json").is_file()
    artifact_types = {artifact.artifact_type for artifact in result.artifacts.values()}
    assert {
        "opportunity_model",
        "outcome_model",
        "capability_graph",
        "decision_graph",
        "scenario_model",
        "autonomous_change",
        "evidence_and_learning",
    } <= artifact_types
    decision_graph = result.artifacts[Stage.DECIDE]
    assert any(path.endswith("decision_graph.html") for path in decision_graph.content_files)
    capability_graph = result.artifacts[Stage.COMPOSE]
    assert any(path.endswith("capability_graph.html") for path in capability_graph.content_files)
    assert result.artifacts[Stage.OBSERVE].contract_item_ids
    execute_calls = [call for call in adapter.calls if call.stage == Stage.EXECUTE.value]
    workspace_call = next(call for call in execute_calls if call.mode.value == "workspace")
    assert workspace_call.permission_profile == "workspace-write"
    review_call = next(
        call for call in execute_calls if "workspace-review" in call.invocation_id
    )
    assert review_call.permission_profile == "read-only"
    assert result.demo.has_video and result.demo.has_audio
    assert result.demo.visual_content
    assert result.demo.duration_seconds < 180

    checksums = json.loads(result.bundle.checksums_path.read_text())
    bundle_manifest = json.loads(result.bundle.manifest_path.read_text())
    assert checksums
    assert bundle_manifest["checksums_digest"] == digest_value(checksums)
    assert "evidence/build_request.json" in checksums
    assert "evidence/settings.snapshot.json" in checksums
    assert not (result.bundle.bundle_dir / "app" / "node_modules").exists()
    for relative, expected in checksums.items():
        assert digest_file(result.bundle.bundle_dir / relative) == expected
    bundled_events = RunStore.read_jsonl(result.bundle.bundle_dir / "evidence" / "events.jsonl")
    assert any(event["event_type"] == "delivery.bundle.completed" for event in bundled_events)
    assert any(event["event_type"] == "delivery.completed" for event in bundled_events)
    bundled_gates = RunStore.read_jsonl(
        result.bundle.bundle_dir / "evidence" / "logs" / "gates.jsonl"
    )
    assert {gate["stage"] for gate in bundled_gates} >= {Stage.DEMO.value, Stage.BUNDLE.value}


def test_gate_failure_preserves_failed_and_repaired_versions(
    tmp_path, repo_root, settings, build_request
) -> None:
    frozen = frozen_fixture(tmp_path, repo_root, settings)
    assert frozen is not None
    result = DeliveryOrchestrator(
        settings=settings, adapter=FixtureAdapter(), base_dir=tmp_path / "runs"
    ).run(
        request=build_request,
        frozen=frozen,
        run_id="delivery_repair",
        fault_stage=Stage.MODEL,
    )
    rows = RunStore.read_jsonl(result.store_root / "logs" / "artifacts.jsonl")
    model = [row for row in rows if row["stage"] == Stage.MODEL.value]
    assert [(row["version"], row["gate_status"]) for row in model] == [(1, "REPAIR"), (2, "PASS")]
    assert len(RunStore.read_jsonl(result.store_root / "logs" / "repairs.jsonl")) == 1


class NeverPassSenseGate(GateEngine):
    def evaluate(self, stage, candidate, peer_findings):
        if stage != Stage.SENSE:
            return super().evaluate(stage, candidate, peer_findings)
        finding = ReviewFinding(
            rule_id="sense.fixture.never_passes",
            severity="high",
            message="Deliberate exhausted-budget fixture.",
        )
        return GateResult(
            stage=stage,
            decision=GateDecision.REPAIR,
            rule_results={finding.rule_id: False},
            findings=[finding],
        )


def test_repair_budget_is_enforced_and_failure_evidence_is_immutable(
    tmp_path, repo_root, settings, build_request
) -> None:
    frozen = frozen_fixture(tmp_path, repo_root, settings)
    assert frozen is not None
    settings.delivery["artifact_stage_repairs"] = 2
    base = tmp_path / "runs"
    with pytest.raises(RuntimeError, match="repair budget exhausted for SENSE"):
        DeliveryOrchestrator(
            settings=settings,
            adapter=FixtureAdapter(),
            base_dir=base,
            gate_engine=NeverPassSenseGate(),
        ).run(request=build_request, frozen=frozen, run_id="delivery_budget_exhausted")
    root = base / "delivery_budget_exhausted"
    repairs = RunStore.read_jsonl(root / "logs" / "repairs.jsonl")
    artifacts = RunStore.read_jsonl(root / "logs" / "artifacts.jsonl")
    sense_versions = [row for row in artifacts if row["stage"] == Stage.SENSE.value]
    assert len(repairs) == 2
    assert [(row["version"], row["gate_status"]) for row in sense_versions] == [
        (1, "REPAIR"),
        (2, "REPAIR"),
        (3, "REPAIR"),
    ]
    assert json.loads((root / "state.json").read_text())["status"] == RunStatus.FAILED.value


class TamperAfterSenseGate(GateEngine):
    def __init__(self, frozen) -> None:
        self.frozen = frozen

    def evaluate(self, stage, candidate, peer_findings):
        result = super().evaluate(stage, candidate, peer_findings)
        if stage == Stage.SENSE:
            first = next(iter(self.frozen.vectors.values()))
            first.vector.trust = 0.75
        return result


def test_delivery_reverifies_frozen_trust_before_every_stage(
    tmp_path, repo_root, settings, build_request
) -> None:
    frozen = frozen_fixture(tmp_path, repo_root, settings)
    assert frozen is not None
    with pytest.raises(ValueError, match="frozen relationship snapshot changed before MODEL"):
        DeliveryOrchestrator(
            settings=settings,
            adapter=FixtureAdapter(),
            base_dir=tmp_path / "runs",
            gate_engine=TamperAfterSenseGate(frozen),
        ).run(request=build_request, frozen=frozen, run_id="delivery_frozen_guard")


def test_intake_gate_rejects_secrets_before_persisting_the_request(
    tmp_path, repo_root, settings
) -> None:
    frozen = frozen_fixture(tmp_path, repo_root, settings)
    assert frozen is not None
    unsafe_request = BuildRequest(
        idea="Create a local operations dashboard",
        audience="Operations teams",
        outcome="Use api_key=sk-abcdefghijklmnopqrstuv to load records",
    )
    base = tmp_path / "runs"
    with pytest.raises(ValueError, match="intake.no_embedded_secrets"):
        DeliveryOrchestrator(
            settings=settings,
            adapter=FixtureAdapter(),
            base_dir=base,
        ).run(request=unsafe_request, frozen=frozen, run_id="delivery_secret_blocked")
    assert not (base / "delivery_secret_blocked" / "build_request.json").exists()


class FailOnceAdapter(FixtureAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 0

    def invoke(self, invocation):
        if invocation.stage == Stage.DECIDE.value and self.failures < 2:
            self.failures += 1
            return AgentResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.BACKEND_ERROR,
                error={"code": "fixture_interruption", "message": "interrupted"},
            )
        return super().invoke(invocation)


class RejectFirstModelHandoffAdapter(FixtureAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.rejected = False

    def invoke(self, invocation):
        if (
            invocation.stage == Stage.MODEL.value
            and "proposal-1" in invocation.invocation_id
            and not self.rejected
        ):
            self.rejected = True
            invocation.fixture_data["output"]["upstream_accepted"] = False
            invocation.fixture_data["output"]["revision_requests"] = [
                "Clarify the primary success signal."
            ]
        return super().invoke(invocation)


def test_rejected_handoff_rewards_zero_and_routes_one_upstream_revision(
    tmp_path, repo_root, settings, build_request
) -> None:
    frozen = frozen_fixture(tmp_path, repo_root, settings)
    assert frozen is not None
    result = DeliveryOrchestrator(
        settings=settings,
        adapter=RejectFirstModelHandoffAdapter(),
        base_dir=tmp_path / "runs",
    ).run(request=build_request, frozen=frozen, run_id="delivery_handoff_revision")
    handoffs = RunStore.read_jsonl(result.store_root / "logs" / "handoff_rewards.jsonl")
    first = next(row for row in handoffs if row["consumer_stage"] == Stage.MODEL.value)
    assert first["reward"] == 0
    assert first["revised"] is True
    sense = [
        row
        for row in RunStore.read_jsonl(result.store_root / "logs" / "artifacts.jsonl")
        if row["stage"] == Stage.SENSE.value and row["gate_status"] == "PASS"
    ]
    assert len(sense) == 2
    negative = [
        row
        for row in RunStore.read_jsonl(
            result.store_root / "logs" / "shadow_trust_updates.jsonl"
        )
        if row["reward"] == 0
    ]
    assert len(negative) == 6
    assert all(row["proposed_delta"] < 0 for row in negative)


def test_failed_run_resumes_from_last_committed_stage_without_duplicates(
    tmp_path, repo_root, settings, build_request
) -> None:
    frozen = frozen_fixture(tmp_path, repo_root, settings)
    assert frozen is not None
    base = tmp_path / "runs"
    with pytest.raises(RuntimeError, match="DECIDE proposal failed"):
        DeliveryOrchestrator(settings=settings, adapter=FailOnceAdapter(), base_dir=base).run(
            request=build_request, frozen=frozen, run_id="delivery_resume"
        )
    failed_state = json.loads((base / "delivery_resume" / "state.json").read_text())
    assert failed_state["completed_stages"] == ["SENSE", "MODEL", "COMPOSE"]

    resumed = DeliveryOrchestrator(
        settings=settings, adapter=FixtureAdapter(), base_dir=base
    ).run(request=build_request, frozen=frozen, run_id="delivery_resume", resume=True)
    assert resumed.status == RunStatus.COMPLETED
    artifacts = RunStore.read_jsonl(resumed.store_root / "logs" / "artifacts.jsonl")
    passing_sense = [
        row
        for row in artifacts
        if row["stage"] == "SENSE" and row["gate_status"] == "PASS"
    ]
    assert len(passing_sense) == 1


def test_emergency_stop_is_a_durable_terminal_state(
    tmp_path, repo_root, settings, build_request
) -> None:
    frozen = frozen_fixture(tmp_path, repo_root, settings)
    assert frozen is not None
    base = tmp_path / "runs"
    with pytest.raises(RunCancelled):
        DeliveryOrchestrator(settings=settings, adapter=FixtureAdapter(), base_dir=base).run(
            request=build_request,
            frozen=frozen,
            run_id="delivery_cancel",
            cancel_check=lambda: True,
        )
    state = json.loads((base / "delivery_cancel" / "state.json").read_text())
    assert state["status"] == RunStatus.CANCELLED.value
    events = RunStore.read_jsonl(base / "delivery_cancel" / "events.jsonl")
    assert events[-1]["event_type"] == "delivery.cancelled"
