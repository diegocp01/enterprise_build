from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zerohandoff.config import SettingsBundle, digest_value
from zerohandoff.delivery.stages import STAGE_SPECS, make_fixture_candidate
from zerohandoff.models import (
    AgentInvocation,
    BuildRequest,
    ExecutionMode,
    FrozenRelationshipSnapshot,
    InvocationStatus,
    RepairPacket,
    ReviewFinding,
    Stage,
)
from zerohandoff.runtime.base import RuntimeAdapter
from zerohandoff.storage import RunStore
from zerohandoff.trust import (
    compile_relationship_policy,
    qualitative_stance_for,
    select_lead,
)


PROPOSAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidate", "rationale"],
    "properties": {
        "candidate": {"type": "object", "additionalProperties": True},
        "rationale": {"type": "string"},
    },
}


def _value_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        item_schema = _value_schema(value[0]) if value else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if isinstance(value, dict):
        properties = {field: _value_schema(item) for field, item in value.items()}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        }
    return {"type": "string"}


def proposal_schema(stage: Stage, exemplar: dict[str, Any]) -> dict[str, Any]:
    properties = {field: _value_schema(value) for field, value in exemplar.items()}
    required = list(
        dict.fromkeys((*STAGE_SPECS[stage].required_fields, *properties.keys()))
    )
    schema = {
        **PROPOSAL_SCHEMA,
        "properties": {
            **PROPOSAL_SCHEMA["properties"],
            "candidate": {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            },
        },
    }
    return schema

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule_id", "severity", "message", "evidence"],
                "properties": {
                    "rule_id": {"type": "string"},
                    "severity": {"enum": ["critical", "high", "medium", "low"]},
                    "message": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}

WORKSPACE_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "changed_files", "contract_item_ids"],
    "properties": {
        "summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "contract_item_ids": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass(frozen=True)
class PairResult:
    stage: Stage
    candidate: dict[str, Any]
    proposals: dict[str, dict[str, Any]]
    lead: str
    peer: str
    selection_reason: str
    inbound_trust: dict[str, float]
    peer_findings: list[ReviewFinding]


class PairRunner:
    def __init__(
        self,
        *,
        settings: SettingsBundle,
        adapter: RuntimeAdapter,
        store: RunStore,
        frozen: FrozenRelationshipSnapshot,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.store = store
        self.frozen = frozen

    def run(
        self,
        *,
        stage: Stage,
        request: BuildRequest,
        previous: dict[Stage, dict[str, Any]],
        attempt: int,
    ) -> PairResult:
        team = self.settings.teams[stage]
        left, right = team.agents
        proposal_outputs: dict[str, dict[str, Any]] = {}
        for agent, peer in ((left, right), (right, left)):
            fixture_candidate = make_fixture_candidate(stage, request, previous, author=agent.name)
            relationship_policy = compile_relationship_policy(
                agent,
                peer,
                self.frozen.vectors,
                self.settings.relationship_policy,
            )
            invocation = AgentInvocation(
                invocation_id=self._id(stage, agent.name, "proposal", attempt),
                run_id=self.store.run_id,
                phase="delivery",
                stage=stage.value,
                pair=stage.value,
                agent=agent,
                attempt=attempt,
                mode=ExecutionMode.ARTIFACT,
                objective=(
                    f"Create an independent {stage.value} proposal from the verified "
                    "upstream artifacts. "
                    "Do not assume or imitate the peer proposal."
                ),
                inputs={
                    "build_request": request.model_dump(mode="json"),
                    "previous_artifacts": {
                        key.value: value for key, value in previous.items()
                    },
                },
                fixture_data={
                    "output": {
                        "candidate": fixture_candidate,
                        "rationale": f"Independent fixture proposal by {agent.name}.",
                    },
                },
                input_digests={
                    "build_request": digest_value(request),
                    "previous": digest_value({key.value: value for key, value in previous.items()}),
                },
                relationship_vector_digest=self.frozen.content_digest,
                trust_stance=qualitative_stance_for(agent, peer, self.frozen.vectors),
                relationship_policy=relationship_policy,
                workspace_root=str(self.store.workspace_dir),
                permission_profile="read-only",
                output_schema=proposal_schema(stage, fixture_candidate),
                model=self.settings.models["delivery"]["model"],
                reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
                timeout_seconds=int(self.settings.training["timeout_seconds"]),
                retry_budget=int(self.settings.delivery["backend_retries_per_call"]),
            )
            result = self._invoke(invocation)
            if result.status != InvocationStatus.COMPLETED or result.output is None:
                raise RuntimeError(f"{stage.value} proposal failed for {agent.name}")
            proposal_outputs[agent.name] = result.output
        decision = select_lead(team, self.frozen.vectors, last_speaker=right.name)
        fixture_integrated = make_fixture_candidate(
            stage, request, previous, author=decision.lead.name
        )
        integration_policy = compile_relationship_policy(
            decision.lead,
            decision.peer,
            self.frozen.vectors,
            self.settings.relationship_policy,
        )
        integration = AgentInvocation(
            invocation_id=self._id(stage, decision.lead.name, "integration", attempt),
            run_id=self.store.run_id,
            phase="delivery",
            stage=stage.value,
            pair=stage.value,
            agent=decision.lead,
            attempt=attempt,
            mode=ExecutionMode.ARTIFACT,
            objective=(
                "Integrate both independent proposals. Record material choices in the candidate "
                "and preserve Outcome Model traceability."
            ),
            inputs={
                "build_request": request.model_dump(mode="json"),
                "proposals": proposal_outputs,
            },
            fixture_data={
                "output": {
                    "candidate": fixture_integrated,
                    "rationale": "Integrated both proposals using the trust-selected lead.",
                },
            },
            input_digests={"proposals": digest_value(proposal_outputs)},
            relationship_vector_digest=self.frozen.content_digest,
            trust_stance=qualitative_stance_for(
                decision.lead, decision.peer, self.frozen.vectors
            ),
            relationship_policy=integration_policy,
            workspace_root=str(self.store.workspace_dir),
            permission_profile="read-only",
            output_schema=proposal_schema(stage, fixture_integrated),
            model=self.settings.models["delivery"]["model"],
            reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=int(self.settings.delivery["backend_retries_per_call"]),
        )
        integrated_result = self._invoke(integration)
        if (
            integrated_result.status != InvocationStatus.COMPLETED
            or integrated_result.output is None
        ):
            raise RuntimeError(f"{stage.value} integration failed")
        candidate = dict(integrated_result.output["candidate"])
        review_policy = compile_relationship_policy(
            decision.peer,
            decision.lead,
            self.frozen.vectors,
            self.settings.relationship_policy,
        )
        review = AgentInvocation(
            invocation_id=self._id(stage, decision.peer.name, "review", attempt),
            run_id=self.store.run_id,
            phase="delivery",
            stage=stage.value,
            pair=stage.value,
            agent=decision.peer,
            attempt=attempt,
            mode=ExecutionMode.REVIEW,
            objective="Audit the integrated candidate for concrete high-impact defects.",
            inputs={
                "candidate": candidate,
            },
            fixture_data={"output": {"findings": []}},
            input_digests={"candidate": digest_value(candidate)},
            relationship_vector_digest=self.frozen.content_digest,
            trust_stance=qualitative_stance_for(
                decision.peer, decision.lead, self.frozen.vectors
            ),
            relationship_policy=review_policy,
            workspace_root=str(self.store.workspace_dir),
            permission_profile="read-only",
            output_schema=REVIEW_SCHEMA,
            model=self.settings.models["delivery"]["model"],
            reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=int(self.settings.delivery["backend_retries_per_call"]),
        )
        reviewed = self._invoke(review)
        if reviewed.status != InvocationStatus.COMPLETED or reviewed.output is None:
            raise RuntimeError(f"{stage.value} peer review failed")
        findings = [ReviewFinding.model_validate(item) for item in reviewed.output["findings"]]
        self.store.append_event(
            event_type="delivery.lead.selected",
            status="completed",
            stage=stage.value,
            actor=decision.lead.name,
            adapter=self.adapter.name,
            relationship_vector_digest=self.frozen.content_digest,
            payload={
                "peer": decision.peer.name,
                "reason": decision.reason,
                "inbound_trust": decision.inbound_trust,
                "last_speaker": right.name,
            },
        )
        return PairResult(
            stage=stage,
            candidate=candidate,
            proposals=proposal_outputs,
            lead=decision.lead.name,
            peer=decision.peer.name,
            selection_reason=decision.reason,
            inbound_trust=decision.inbound_trust,
            peer_findings=findings,
        )

    def implement_workspace(
        self,
        *,
        workspace: Path,
        request: BuildRequest,
        candidate: dict[str, Any],
        lead_name: str,
    ) -> dict[str, Any]:
        team = self.settings.teams[Stage.EXECUTE]
        lead = next(agent for agent in team.agents if agent.name == lead_name)
        peer = next(agent for agent in team.agents if agent.name != lead_name)
        relationship_policy = compile_relationship_policy(
            lead,
            peer,
            self.frozen.vectors,
            self.settings.relationship_policy,
        )
        invocation = AgentInvocation(
            invocation_id=self._id(Stage.EXECUTE, lead.name, "workspace", 1),
            run_id=self.store.run_id,
            phase="delivery",
            stage=Stage.EXECUTE.value,
            pair=Stage.EXECUTE.value,
            agent=lead,
            mode=ExecutionMode.WORKSPACE,
            objective=(
                "Implement the verified Outcome Model in the existing React/Vite scaffold. "
                "Work only inside the current application workspace, preserve "
                "project_commands.json, and return a typed change report."
            ),
            inputs={
                "build_request": request.model_dump(mode="json"),
                "build_steps": candidate.get("build_steps", []),
                "contract_item_ids": candidate.get("contract_item_ids", []),
            },
            fixture_data={
                "output": {
                    "summary": "Fixture workspace already implements the bounded product profile.",
                    "changed_files": [],
                    "contract_item_ids": candidate.get("contract_item_ids", []),
                }
            },
            relationship_vector_digest=self.frozen.content_digest,
            trust_stance=qualitative_stance_for(lead, peer, self.frozen.vectors),
            relationship_policy=relationship_policy,
            workspace_root=str(workspace),
            permission_profile="workspace-write",
            output_schema=WORKSPACE_RESULT_SCHEMA,
            model=self.settings.models["delivery"]["model"],
            reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=int(self.settings.delivery["backend_retries_per_call"]),
        )
        result = self._invoke(invocation)
        if result.status != InvocationStatus.COMPLETED or result.output is None:
            raise RuntimeError("EXECUTE workspace implementation failed")
        return dict(result.output)

    def review_workspace(
        self,
        *,
        workspace: Path,
        candidate: dict[str, Any],
        implementer_report: dict[str, Any],
        peer_name: str,
        lead_name: str,
        attempt: int,
    ) -> list[ReviewFinding]:
        team = self.settings.teams[Stage.EXECUTE]
        peer = next(agent for agent in team.agents if agent.name == peer_name)
        lead = next(agent for agent in team.agents if agent.name == lead_name)
        relationship_policy = compile_relationship_policy(
            peer,
            lead,
            self.frozen.vectors,
            self.settings.relationship_policy,
        )
        invocation = AgentInvocation(
            invocation_id=self._id(Stage.EXECUTE, peer.name, "workspace-review", attempt),
            run_id=self.store.run_id,
            phase="delivery",
            stage=Stage.EXECUTE.value,
            pair=Stage.EXECUTE.value,
            agent=peer,
            attempt=attempt,
            mode=ExecutionMode.REVIEW,
            objective=(
                "Inspect the actual generated application workspace read-only. Report only "
                "concrete contract, safety, or reproducibility defects."
            ),
            inputs={
                "candidate": candidate,
                "implementer_report": implementer_report,
            },
            fixture_data={"output": {"findings": []}},
            relationship_vector_digest=self.frozen.content_digest,
            trust_stance=qualitative_stance_for(peer, lead, self.frozen.vectors),
            relationship_policy=relationship_policy,
            workspace_root=str(workspace),
            permission_profile="read-only",
            output_schema=REVIEW_SCHEMA,
            model=self.settings.models["delivery"]["model"],
            reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=int(self.settings.delivery["backend_retries_per_call"]),
        )
        result = self._invoke(invocation)
        if result.status != InvocationStatus.COMPLETED or result.output is None:
            raise RuntimeError("EXECUTE workspace review failed")
        return [ReviewFinding.model_validate(item) for item in result.output["findings"]]

    def repair(
        self,
        *,
        packet: RepairPacket,
        request: BuildRequest,
        previous: dict[Stage, dict[str, Any]],
        lead_name: str,
    ) -> dict[str, Any]:
        team = self.settings.teams[packet.stage]
        lead = next(agent for agent in team.agents if agent.name == lead_name)
        peer = next(agent for agent in team.agents if agent.name != lead_name)
        relationship_policy = compile_relationship_policy(
            lead,
            peer,
            self.frozen.vectors,
            self.settings.relationship_policy,
        )
        fixed = make_fixture_candidate(packet.stage, request, previous, author=lead.name)
        is_build = packet.stage == Stage.EXECUTE
        invocation = AgentInvocation(
            invocation_id=self._id(packet.stage, lead.name, "repair", packet.attempt),
            run_id=self.store.run_id,
            phase="delivery",
            stage=packet.stage.value,
            pair=packet.stage.value,
            agent=lead,
            attempt=packet.attempt,
            mode=ExecutionMode.WORKSPACE if is_build else ExecutionMode.ARTIFACT,
            objective=(
                "Repair only the failed gate conditions in the supplied packet. For EXECUTE, "
                "edit only the current application workspace and preserve project_commands.json."
            ),
            inputs={
                "repair_packet": packet.model_dump(mode="json"),
                "build_request": request.model_dump(mode="json"),
                "previous_artifacts": {
                    stage.value: artifact for stage, artifact in previous.items()
                },
            },
            fixture_data={
                "output": {
                    "candidate": fixed,
                    "rationale": "Fixture repair restored required contract fields.",
                },
            },
            relationship_vector_digest=self.frozen.content_digest,
            trust_stance=qualitative_stance_for(lead, peer, self.frozen.vectors),
            relationship_policy=relationship_policy,
            workspace_root=str(
                self.store.workspace_dir / "app" if is_build else self.store.workspace_dir
            ),
            permission_profile="workspace-write" if is_build else "read-only",
            output_schema=proposal_schema(packet.stage, fixed),
            model=self.settings.models["delivery"]["model"],
            reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=int(self.settings.delivery["backend_retries_per_call"]),
        )
        result = self._invoke(invocation)
        if result.status != InvocationStatus.COMPLETED or result.output is None:
            raise RuntimeError(f"repair failed for {packet.stage.value}")
        return dict(result.output["candidate"])

    def _log(self, invocation: AgentInvocation, result: Any) -> None:
        self.store.append_log(
            "agent_calls",
            {
                "invocation": invocation.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            },
        )
        if invocation.relationship_policy is not None:
            self.store.append_log(
                "relationship_policies",
                {
                    "invocation_id": invocation.invocation_id,
                    "stage": invocation.stage,
                    "team_id": invocation.pair,
                    "actor": invocation.agent.name,
                    "mode": invocation.mode.value,
                    "settings_digest": self.settings.digest,
                    "relationship_vector_digest": self.frozen.content_digest,
                    "policy": invocation.relationship_policy.model_dump(mode="json"),
                },
            )
        self.store.append_event(
            event_type="agent.invocation.completed",
            status=result.status.value,
            stage=invocation.stage,
            actor=invocation.agent.name,
            adapter=self.adapter.name,
            model=invocation.model,
            reasoning_effort=invocation.reasoning_effort,
            relationship_vector_digest=self.frozen.content_digest,
            attempt=invocation.attempt,
            duration_ms=result.duration_ms,
            usage=result.usage,
            error=result.error,
        )

    def _invoke(self, invocation: AgentInvocation) -> Any:
        if invocation.relationship_policy is not None:
            invocation.input_digests.setdefault(
                "relationship_policy",
                digest_value(invocation.relationship_policy),
            )
        result = None
        for retry in range(invocation.retry_budget + 1):
            current = invocation if retry == 0 else invocation.model_copy(
                update={
                    "invocation_id": f"{invocation.invocation_id}-retry{retry}",
                    "attempt": invocation.attempt + retry,
                    "retry_budget": 0,
                }
            )
            result = self.adapter.invoke(current)
            self._log(current, result)
            if result.status == InvocationStatus.COMPLETED:
                return result
            if result.status == InvocationStatus.CANCELLED:
                return result
        assert result is not None
        return result

    def _id(self, stage: Stage, agent: str, operation: str, attempt: int) -> str:
        suffix = uuid.uuid4().hex[:6]
        return f"{self.store.run_id}-{stage.value}-{agent}-{operation}-{attempt}-{suffix}"
