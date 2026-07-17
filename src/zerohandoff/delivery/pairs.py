from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zerohandoff.config import SettingsBundle, digest_file, digest_value
from zerohandoff.delivery.commands import approved_execute_commands
from zerohandoff.delivery.stages import STAGE_SPECS, make_fixture_candidate
from zerohandoff.delivery.workspace import BROWSER_ACCEPTANCE_HARNESS_REQUIREMENTS
from zerohandoff.models import (
    AgentIdentity,
    AgentInvocation,
    BuildRequest,
    ExecutionMode,
    GateDecision,
    InferenceLearningState,
    InferenceNightOutput,
    InvocationStatus,
    Personality,
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
    "required": [
        "candidate",
        "rationale",
        "upstream_accepted",
        "revision_requests",
        "memory_observations",
    ],
    "properties": {
        "candidate": {"type": "object", "additionalProperties": True},
        "rationale": {"type": "string"},
        "upstream_accepted": {"type": "boolean"},
        "revision_requests": {"type": "array", "items": {"type": "string"}},
        "memory_observations": {"type": "array", "items": {"type": "string"}},
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
    if stage in {Stage.DECIDE, Stage.EXECUTE} and exemplar.get("commands"):
        command_schema = properties["commands"]
        command_schema["properties"] = {
            name: {"type": "string", "enum": [command]}
            for name, command in exemplar["commands"].items()
        }
    if stage == Stage.OBSERVE and exemplar.get("proof_entries"):
        properties["proof_entries"]["items"]["properties"]["status"] = {
            "type": "string",
            "enum": ["pass", "passed", "failed"],
        }
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

INFERENCE_NIGHT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "trust_commit_approved",
        "memory_updates",
        "blocking_issues",
        "resolved_issues",
        "decision_reasons",
        "evidence",
        "confidence",
    ],
    "properties": {
        "trust_commit_approved": {"type": "boolean"},
        "memory_updates": {"type": "object"},
        "blocking_issues": {"type": "array", "items": {"type": "string"}},
        "resolved_issues": {"type": "array", "items": {"type": "string"}},
        "decision_reasons": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}


def inference_night_schema(agent_names: list[str]) -> dict[str, Any]:
    """Return a strict structured-output schema with one memory slot per agent."""

    memory_properties = {
        name: {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string"},
        }
        for name in agent_names
    }
    return {
        **INFERENCE_NIGHT_SCHEMA,
        "properties": {
            **INFERENCE_NIGHT_SCHEMA["properties"],
            "memory_updates": {
                "type": "object",
                "additionalProperties": False,
                "required": agent_names,
                "properties": memory_properties,
            },
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
    upstream_assessments: dict[str, bool]
    revision_requests: dict[str, list[str]]
    memory_observations: dict[str, list[str]]


class PairRunner:
    def __init__(
        self,
        *,
        settings: SettingsBundle,
        adapter: RuntimeAdapter,
        store: RunStore,
        learning_state: InferenceLearningState,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.store = store
        self.learning_state = learning_state
        self.relationships = learning_state.vectors
        self.relationship_digest = learning_state.content_digest
        self.memories = learning_state.memories

    def run(
        self,
        *,
        stage: Stage,
        request: BuildRequest,
        previous: dict[Stage, dict[str, Any]],
        attempt: int,
        upstream_pair: tuple[str, str] | None = None,
    ) -> PairResult:
        team = self.settings.teams[stage]
        left, right = team.agents
        prior_gate_findings = self._recent_gate_findings(stage)
        proposal_outputs: dict[str, dict[str, Any]] = {}
        for agent, peer in ((left, right), (right, left)):
            fixture_candidate = make_fixture_candidate(stage, request, previous, author=agent.name)
            relationship_policy = compile_relationship_policy(
                agent,
                peer,
                self.relationships,
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
                mode=(
                    ExecutionMode.WORKSPACE
                    if stage == Stage.OBSERVE
                    else ExecutionMode.ARTIFACT
                ),
                objective=(
                    f"Create an independent {stage.value} proposal from the verified "
                    "upstream artifacts. First assess whether the immediately upstream "
                    "team's output is usable: set upstream_accepted=false and request a "
                    "concrete revision when it is not. Resolve every supplied "
                    "prior_stage_gate_finding instead of repeating a failed attempt. "
                    + (
                        "For OBSERVE, make demo_plan an executable end-to-end browser tour. "
                        "Inspect the delivered production UI in the read-only app workspace and "
                        "use its exact accessible roles and names. Include at least two real "
                        "click, select, or fill actions, with "
                        "visible postcondition waits. Each action needs type, accessible role, "
                        "accessible name, and value; selectors are allowed only for non-mutating "
                        "observations. Supported types are exactly click, select, fill, scroll, "
                        "and wait. The recorder has already opened the app: never emit navigate, "
                        "inspect, assert_*, or any other action type. Express a positive visual "
                        "assertion as a wait; for an attribute state, use a non-mutating selector "
                        "wait while still including role, name, and value keys. Every step needs "
                        "a non-empty expected state and at least one action. Only when the actual "
                        "product already exposes editable or validated input, demonstrate invalid "
                        "input, preserved state, correction, and reset with visible waits. If it "
                        "does not expose such input, demonstrate an existing guarded or blocked "
                        "action or impossible transition, show that state is preserved, then take "
                        "the valid corrective action and reset. Never invent an input, validation "
                        "rule, or new product capability merely to satisfy the demo plan. "
                        "A wait must observe something present; never encode absent, missing, "
                        "not present, no error, or another negative assertion. Prove recovery "
                        "with positive accepted-result and live-status text instead. For every "
                        "wait, put the exact accessible name or visible text to locate in name "
                        "(or use selector); value must be numeric milliseconds such as 900. "
                        "Use only real ARIA roles present in the UI. For plain visible text set "
                        "role to the empty string; for CSS set role and name empty and put CSS "
                        "in selector. Never invent roles such as text or selector. "
                        "Never hide an assertion description inside value because the recorder "
                        "uses that field only as timing. Treat a "
                        "successful browser_acceptance_receipt as resolved production-browser "
                        "evidence cited by that command's stdout digest. "
                        "For every proof entry, status must be exactly pass or passed only when "
                        "the cited evidence proves the contract item; otherwise use failed. "
                        "Never use ambiguous labels such as verified. "
                        "For upstream_accepted, judge only whether EXECUTE's delivered app or "
                        "committed evidence has a concrete producer defect that requires EXECUTE "
                        "to revise. An omitted, malformed, or not-yet-authored OBSERVE demo action "
                        "is this consumer team's own plan defect: fix it here and do not route it "
                        "upstream. "
                        "Write narration_script as polished presenter copy synchronized to the "
                        "visible workflow; never write directions to the video producer. "
                        if stage == Stage.OBSERVE
                        else ""
                    )
                    +
                    "Do not assume or imitate the peer proposal."
                ),
                inputs={
                    "build_request": request.model_dump(mode="json"),
                    "previous_artifacts": {
                        key.value: value for key, value in previous.items()
                    },
                    "upstream_relationship_guidance": self._upstream_guidance(
                        agent, upstream_pair
                    ),
                    "persistent_memory": self.memories.get(agent.name, []),
                    "prior_stage_gate_findings": prior_gate_findings,
                },
                fixture_data={
                    "output": {
                        "candidate": fixture_candidate,
                        "rationale": f"Independent fixture proposal by {agent.name}.",
                        "upstream_accepted": True,
                        "revision_requests": [],
                        "memory_observations": [],
                    },
                },
                input_digests={
                    "build_request": digest_value(request),
                    "previous": digest_value({key.value: value for key, value in previous.items()}),
                },
                relationship_vector_digest=self.relationship_digest,
                trust_stance=qualitative_stance_for(agent, peer, self.relationships),
                relationship_policy=relationship_policy,
                workspace_root=str(
                    self.store.workspace_dir / "app"
                    if stage == Stage.OBSERVE
                    else self.store.workspace_dir
                ),
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
            output = dict(result.output)
            output["candidate"] = self._hydrate_execute_commands(
                stage, dict(output["candidate"]), previous
            )
            proposal_outputs[agent.name] = output
        decision = select_lead(team, self.relationships, last_speaker=right.name)
        fixture_integrated = make_fixture_candidate(
            stage, request, previous, author=decision.lead.name
        )
        integration_policy = compile_relationship_policy(
            decision.lead,
            decision.peer,
            self.relationships,
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
                "and preserve Outcome Model traceability. Resolve every supplied prior-stage "
                "gate finding and do not reintroduce a previously rejected shape."
            ),
            inputs={
                "build_request": request.model_dump(mode="json"),
                "previous_artifacts": {
                    key.value: value for key, value in previous.items()
                },
                "proposals": proposal_outputs,
                "persistent_memory": self.memories.get(decision.lead.name, []),
                "prior_stage_gate_findings": prior_gate_findings,
            },
            fixture_data={
                "output": {
                    "candidate": fixture_integrated,
                    "rationale": "Integrated both proposals using the trust-selected lead.",
                    "upstream_accepted": all(
                        bool(output["upstream_accepted"])
                        for output in proposal_outputs.values()
                    ),
                    "revision_requests": [
                        request
                        for output in proposal_outputs.values()
                        for request in output["revision_requests"]
                    ],
                    "memory_observations": [
                        item
                        for output in proposal_outputs.values()
                        for item in output["memory_observations"]
                    ],
                },
            },
            input_digests={
                "build_request": digest_value(request),
                "previous": digest_value(
                    {key.value: value for key, value in previous.items()}
                ),
                "proposals": digest_value(proposal_outputs),
            },
            relationship_vector_digest=self.relationship_digest,
            trust_stance=qualitative_stance_for(
                decision.lead, decision.peer, self.relationships
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
        candidate = self._hydrate_execute_commands(
            stage, dict(integrated_result.output["candidate"]), previous
        )
        review_policy = compile_relationship_policy(
            decision.peer,
            decision.lead,
            self.relationships,
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
            objective=(
                "Audit the integrated candidate for concrete high-impact defects within this "
                "stage's artifact boundary, including recurrence of supplied prior-stage gate "
                f"findings. {self._review_boundary(stage)}"
            ),
            inputs={
                "build_request": request.model_dump(mode="json"),
                "previous_artifacts": {
                    key.value: value for key, value in previous.items()
                },
                "candidate": candidate,
                "persistent_memory": self.memories.get(decision.peer.name, []),
                "prior_stage_gate_findings": prior_gate_findings,
            },
            fixture_data={"output": {"findings": []}},
            input_digests={
                "build_request": digest_value(request),
                "previous": digest_value(
                    {key.value: value for key, value in previous.items()}
                ),
                "candidate": digest_value(candidate),
            },
            relationship_vector_digest=self.relationship_digest,
            trust_stance=qualitative_stance_for(
                decision.peer, decision.lead, self.relationships
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
            relationship_vector_digest=self.relationship_digest,
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
            upstream_assessments={
                name: bool(output["upstream_accepted"])
                for name, output in proposal_outputs.items()
            },
            revision_requests={
                name: list(output["revision_requests"])
                for name, output in proposal_outputs.items()
            },
            memory_observations={
                name: list(output["memory_observations"])
                for name, output in proposal_outputs.items()
            },
        )

    def implement_workspace(
        self,
        *,
        workspace: Path,
        request: BuildRequest,
        candidate: dict[str, Any],
        previous: dict[Stage, dict[str, Any]],
        lead_name: str,
    ) -> dict[str, Any]:
        team = self.settings.teams[Stage.EXECUTE]
        lead = next(agent for agent in team.agents if agent.name == lead_name)
        peer = next(agent for agent in team.agents if agent.name != lead_name)
        execute_gate_rows = [
            row
            for row in self.store.read_jsonl(self.store.logs_dir / "gates.jsonl")
            if row.get("stage") == Stage.EXECUTE.value
            and row.get("decision") == GateDecision.REPAIR.value
        ]
        prior_execute_findings = [
            finding
            for row in execute_gate_rows[-3:]
            for finding in row.get("findings", [])
            if isinstance(finding, dict)
        ]
        failed_command_rows = [
            row
            for row in self.store.read_jsonl(self.store.logs_dir / "commands.jsonl")
            if row.get("exit_code") != 0
            or row.get("timed_out")
            or row.get("reported_error")
        ][-4:]
        prior_command_failures = [
            {
                "command": row.get("command"),
                "exit_code": row.get("exit_code"),
                "timed_out": bool(row.get("timed_out")),
                "reported_error": row.get("reported_error"),
                "stdout_tail": str(row.get("stdout_tail", ""))[-2000:],
                "stderr_tail": str(row.get("stderr_tail", ""))[-2000:],
                "timestamp": row.get("timestamp"),
            }
            for row in failed_command_rows
        ]
        relationship_policy = compile_relationship_policy(
            lead,
            peer,
            self.relationships,
            self.settings.relationship_policy,
        )
        source_before = digest_value(
            {
                str(path.relative_to(workspace)): digest_file(path)
                for path in workspace.rglob("*")
                if path.is_file()
                and "node_modules" not in path.parts
                and "dist" not in path.relative_to(workspace).parts
                and path.name != "tsconfig.tsbuildinfo"
            }
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
                "Inspect the existing React/Vite application first. If it already satisfies "
                "the verified Outcome Model and its declared test, typecheck, and build "
                "commands pass, preserve it and return the typed change report immediately. "
                "Before declaring it complete, resolve every supplied prior_execute_finding "
                "and prior_command_failure against the actual workspace; these are canonical "
                "failed-run receipts, not optional suggestions. "
                "Otherwise implement only the missing or defective behavior. Work only "
                "inside the current application workspace and preserve project_commands.json. "
                "Preserve the complete allowlisted package script surface: for a live app, "
                "test=`bash tests/browser.acceptance.sh`, with that wrapper clearing stale "
                "receipts first and then running Vitest, the production build, and browser "
                "acceptance; typecheck=`tsc -b --pretty false`, and "
                "build=`tsc -b && vite build`; never delete one script while fixing another. "
                "Every delivered app must include a declared production-browser acceptance "
                "harness under tests/ and execute it from npm test. The harness must exercise "
                "the primary workflow from initial state through visible successful output, "
                "plus the contract's critical cancellation, reset, responsive, keyboard, "
                "and persistence/network guarantees. "
                f"{BROWSER_ACCEPTANCE_HARNESS_REQUIREMENTS} "
                "Every browser acceptance harness must propagate semantic failures as a "
                "nonzero exit. If it uses @playwright/cli run-code, pass one async page "
                "function, reject an exact ### Error output line, and require the receipt "
                "ZEROHANDOFF_BROWSER_ACCEPTANCE_OK before succeeding. "
                "Pin every dependency exactly and use a release with no high or critical "
                "production npm advisories and no critical toolchain advisories. Orchestration "
                "runs production and full-tree audits after installation and treats threshold "
                "findings as build defects. "
                "If that harness selects a named system browser, document the browser and "
                "its clean-machine prerequisite in README.md. Numeric editors may preserve "
                "temporary typing states, but must normalize valid committed or blurred "
                "values to the canonical precision required by the Outcome Model. Explain "
                "any displayed formula operators, caps, floors, or max/min safeguards in "
                "plain language beside the calculation. Derive tests from the complete "
                "Outcome Model matrix: happy path, every declared bound, invalid isolation "
                "and recovery, ties, rounding thresholds, reset/reload equivalence, keyboard "
                "operation, responsive parity, and runtime-network policing when applicable. "
                "For percentage or ratio logic, explicitly test a nonempty 0% lower bound, "
                "a 100% upper bound, and an exact .5 rounding tie in addition to empty input. "
                "Do not install dependencies or run the full validation suite; orchestration "
                "owns those deterministic steps. Return the typed change report immediately "
                "after implementation."
            ),
            inputs={
                "build_request": request.model_dump(mode="json"),
                "previous_artifacts": {
                    key.value: value for key, value in previous.items()
                },
                "execute_candidate": candidate,
                "build_steps": candidate.get("build_steps", []),
                "contract_item_ids": candidate.get("contract_item_ids", []),
                "persistent_memory": self.memories.get(lead.name, []),
                "prior_execute_findings": prior_execute_findings,
                "prior_command_failures": prior_command_failures,
            },
            fixture_data={
                "output": {
                    "summary": "Fixture workspace already implements the bounded product profile.",
                    "changed_files": [],
                    "contract_item_ids": candidate.get("contract_item_ids", []),
                }
            },
            input_digests={
                "build_request": digest_value(request),
                "previous": digest_value(
                    {key.value: value for key, value in previous.items()}
                ),
                "candidate": digest_value(candidate),
            },
            relationship_vector_digest=self.relationship_digest,
            trust_stance=qualitative_stance_for(lead, peer, self.relationships),
            relationship_policy=relationship_policy,
            workspace_root=str(workspace),
            permission_profile="workspace-write",
            output_schema=WORKSPACE_RESULT_SCHEMA,
            model=self.settings.models["delivery"]["model"],
            reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=0,
        )
        result = self._invoke(invocation)
        source_after = digest_value(
            {
                str(path.relative_to(workspace)): digest_file(path)
                for path in workspace.rglob("*")
                if path.is_file()
                and "node_modules" not in path.parts
                and "dist" not in path.relative_to(workspace).parts
                and path.name != "tsconfig.tsbuildinfo"
            }
        )
        if (
            result.status == InvocationStatus.TIMEOUT
            and (workspace / "package.json").is_file()
            and source_after != source_before
        ):
            changed_files = sorted(
                str(path.relative_to(workspace))
                for path in workspace.rglob("*")
                if path.is_file()
                and "node_modules" not in path.parts
                and "dist" not in path.relative_to(workspace).parts
            )
            return {
                "summary": (
                    "Workspace agent timed out after writing files; deterministic commands "
                    "and the independent workspace review must decide acceptance."
                ),
                "changed_files": changed_files,
                "contract_item_ids": candidate.get("contract_item_ids", []),
            }
        if result.status != InvocationStatus.COMPLETED or result.output is None:
            raise RuntimeError("EXECUTE workspace implementation failed")
        return dict(result.output)

    def review_workspace(
        self,
        *,
        workspace: Path,
        request: BuildRequest,
        previous: dict[Stage, dict[str, Any]],
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
            self.relationships,
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
                "concrete contract, safety, accessibility, or reproducibility defects. "
                "Check canonical display normalization after numeric commit/blur, plain-language "
                "formula explanations, coverage of every Outcome Model bound/tie/rounding/reset "
                "case, a declared production-browser acceptance journey invoked by npm test, "
                "and README disclosure of any named browser prerequisite. Reject a browser "
                "harness that violates any of these requirements: "
                f"{BROWSER_ACCEPTANCE_HARNESS_REQUIREMENTS} "
                "The orchestrator invalidates every prior receipt before validation, runs "
                "typecheck and build before npm test, and accepts a browser receipt only from "
                "that final npm test command. Judge the recorded transaction; do not invent a "
                "later command after its final receipt-owning journey."
            ),
            inputs={
                "build_request": request.model_dump(mode="json"),
                "previous_artifacts": {
                    key.value: value for key, value in previous.items()
                },
                "candidate": candidate,
                "implementer_report": implementer_report,
                "persistent_memory": self.memories.get(peer.name, []),
            },
            fixture_data={"output": {"findings": []}},
            input_digests={
                "build_request": digest_value(request),
                "previous": digest_value(
                    {key.value: value for key, value in previous.items()}
                ),
                "candidate": digest_value(candidate),
            },
            relationship_vector_digest=self.relationship_digest,
            trust_stance=qualitative_stance_for(peer, lead, self.relationships),
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
            self.relationships,
            self.settings.relationship_policy,
        )
        fixed = make_fixture_candidate(packet.stage, request, previous, author=lead.name)
        is_build = packet.stage == Stage.EXECUTE
        source_before = None
        failed_workspace_digests: dict[str, str] = {}
        if is_build:
            source_before = digest_value(
                {
                    str(path.relative_to(self.store.workspace_dir / "app")): digest_file(path)
                    for path in (self.store.workspace_dir / "app").rglob("*")
                    if path.is_file()
                    and "node_modules" not in path.parts
                    and "dist"
                    not in path.relative_to(self.store.workspace_dir / "app").parts
                    and path.name != "tsconfig.tsbuildinfo"
                }
            )
            for relative in packet.artifact_refs:
                artifact_path = self.store.root / relative
                if artifact_path.suffix != ".json" or not artifact_path.is_file():
                    continue
                try:
                    failed_workspace_digests = json.loads(
                        artifact_path.read_text()
                    ).get("build_evidence", {}).get("file_digests", {})
                except (json.JSONDecodeError, AttributeError):
                    pass
                if failed_workspace_digests:
                    break
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
                "Repair every failed gate condition in the supplied packet while preserving "
                "all previously valid requirements. Treat every earlier packet finding as a "
                "regression guard, and return a complete replacement artifact rather than a "
                "partial patch that drops IDs, fields, done conditions, or semantic invariants. "
                "For EXECUTE, "
                "edit only the current application workspace, preserve project_commands.json, "
                "keep the allowlisted live-app test command exactly `bash "
                "tests/browser.acceptance.sh`; the wrapper must clear stale receipts first, "
                "run Vitest, build the current production bundle, and then launch a preview. "
                "A local Blob download must remain valid "
                "until the real browser has accepted it rather than revoking its URL immediately. "
                f"{BROWSER_ACCEPTANCE_HARNESS_REQUIREMENTS} "
                "ensure browser acceptance tests propagate semantic failures (including an "
                "exact ### Error line) and require ZEROHANDOFF_BROWSER_ACCEPTANCE_OK, "
                "do not install dependencies or run the full validation suite, and return the "
                "typed repair result immediately after editing. For OBSERVE, preserve exact "
                "proof coverage and use only click, select, fill, scroll, and wait demo actions. "
                "Set each proof status to exactly pass or passed only when its evidence proves "
                "the contract item; otherwise use failed, never verified. "
                "Never emit navigate, inspect, assert_*, or another action type; convert every "
                "positive observation to a visible wait. Never use a wait for absence or a "
                "negative assertion; prove recovery with visible accepted results or status. "
                "A wait's name or selector must identify the real visible target and its value "
                "must be numeric milliseconds; never put assertion prose in value. "
                "Use real ARIA roles only: plain text uses role='', and CSS uses selector with "
                "role='' and name=''; never use invented text or selector roles. "
                "Every action must contain role, name, "
                "and value keys, every step must have a non-empty expected state and actions, "
                "and the full plan must include at least two click/select/fill actions."
            ),
            inputs={
                "repair_packet": packet.model_dump(mode="json"),
                "build_request": request.model_dump(mode="json"),
                "previous_artifacts": {
                    stage.value: artifact for stage, artifact in previous.items()
                },
                "persistent_memory": self.memories.get(lead.name, []),
            },
            fixture_data={
                "output": {
                    "candidate": fixed,
                    "rationale": "Fixture repair restored required contract fields.",
                    "upstream_accepted": True,
                    "revision_requests": [],
                    "memory_observations": [],
                },
            },
            relationship_vector_digest=self.relationship_digest,
            trust_stance=qualitative_stance_for(lead, peer, self.relationships),
            relationship_policy=relationship_policy,
            workspace_root=str(
                self.store.workspace_dir / "app" if is_build else self.store.workspace_dir
            ),
            permission_profile="workspace-write" if is_build else "read-only",
            output_schema=proposal_schema(packet.stage, fixed),
            model=self.settings.models["delivery"]["model"],
            reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=(
                0
                if is_build
                else int(self.settings.delivery["backend_retries_per_call"])
            ),
        )
        result = self._invoke(invocation)
        if is_build and result.status == InvocationStatus.TIMEOUT:
            app_dir = self.store.workspace_dir / "app"
            current_files = {
                str(path.relative_to(app_dir)): digest_file(path)
                for path in app_dir.rglob("*")
                if path.is_file()
                and "node_modules" not in path.parts
                and "dist" not in path.relative_to(app_dir).parts
                and path.name != "tsconfig.tsbuildinfo"
            }
            source_after = digest_value(current_files)
            changed_since_failed_artifact = bool(failed_workspace_digests) and any(
                current_files.get(relative) != expected
                for relative, expected in failed_workspace_digests.items()
                if "dist/" not in relative and "node_modules/" not in relative
            )
            if source_after != source_before or changed_since_failed_artifact:
                return fixed
        if result.status != InvocationStatus.COMPLETED or result.output is None:
            raise RuntimeError(f"repair failed for {packet.stage.value}")
        return self._hydrate_execute_commands(
            packet.stage, dict(result.output["candidate"]), previous
        )

    @staticmethod
    def _hydrate_execute_commands(
        stage: Stage,
        candidate: dict[str, Any],
        previous: dict[Stage, dict[str, Any]],
    ) -> dict[str, Any]:
        """Make DECIDE, not an EXECUTE agent, authoritative for commands.

        Hydration occurs before integration and peer review as well as after a
        repair. An unapproved or absent upstream map clears the field, allowing
        the deterministic gate to fail closed instead of preserving agent text.
        """

        if stage != Stage.EXECUTE:
            return candidate
        decide_commands = (previous.get(Stage.DECIDE) or {}).get("commands")
        candidate["commands"] = approved_execute_commands(decide_commands) or {}
        return candidate

    def review_repaired_candidate(
        self,
        *,
        packet: RepairPacket,
        request: BuildRequest,
        previous: dict[Stage, dict[str, Any]],
        candidate: dict[str, Any],
        lead_name: str,
    ) -> list[ReviewFinding]:
        team = self.settings.teams[packet.stage]
        lead = next(agent for agent in team.agents if agent.name == lead_name)
        peer = next(agent for agent in team.agents if agent.name != lead_name)
        relationship_policy = compile_relationship_policy(
            peer,
            lead,
            self.relationships,
            self.settings.relationship_policy,
        )
        invocation = AgentInvocation(
            invocation_id=self._id(
                packet.stage,
                peer.name,
                "repair-review",
                packet.attempt,
            ),
            run_id=self.store.run_id,
            phase="delivery",
            stage=packet.stage.value,
            pair=packet.stage.value,
            agent=peer,
            attempt=packet.attempt,
            mode=ExecutionMode.REVIEW,
            objective=(
                "Re-review the repaired candidate against the Build Request, verified upstream "
                "artifacts, and every finding in the cumulative repair packet. Explicitly recheck "
                "each packet rule against the complete repaired artifact; report any unresolved "
                "condition or regression, including a field or semantic invariant that an earlier "
                "repair had restored. Report any remaining or regressed defects "
                f"within the stage boundary. {self._review_boundary(packet.stage)}"
            ),
            inputs={
                "repair_packet": packet.model_dump(mode="json"),
                "build_request": request.model_dump(mode="json"),
                "previous_artifacts": {
                    stage.value: artifact for stage, artifact in previous.items()
                },
                "candidate": candidate,
                "persistent_memory": self.memories.get(peer.name, []),
            },
            fixture_data={"output": {"findings": []}},
            input_digests={
                "build_request": digest_value(request),
                "previous": digest_value(
                    {stage.value: artifact for stage, artifact in previous.items()}
                ),
                "candidate": digest_value(candidate),
                "repair_packet": digest_value(packet),
            },
            relationship_vector_digest=self.relationship_digest,
            trust_stance=qualitative_stance_for(peer, lead, self.relationships),
            relationship_policy=relationship_policy,
            workspace_root=str(self.store.workspace_dir),
            permission_profile="read-only",
            output_schema=REVIEW_SCHEMA,
            model=self.settings.models["delivery"]["model"],
            reasoning_effort=self.settings.models["delivery"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=int(self.settings.delivery["backend_retries_per_call"]),
        )
        result = self._invoke(invocation)
        if result.status != InvocationStatus.COMPLETED or result.output is None:
            raise RuntimeError(f"repair review failed for {packet.stage.value}")
        return [
            ReviewFinding.model_validate(item) for item in result.output["findings"]
        ]

    @staticmethod
    def _review_boundary(stage: Stage) -> str:
        boundaries = {
            Stage.SENSE: (
                "SENSE is an opportunity model; assess intent, boundaries, assumptions, and "
                "observable success, not downstream product contracts or code."
            ),
            Stage.MODEL: (
                "MODEL is an outcome contract; assess deterministic semantics and acceptance "
                "criteria, not screen design or implementation."
            ),
            Stage.COMPOSE: (
                "COMPOSE is an interaction-design specification and visual flowboard. It may be "
                "static HTML. Assess journeys, screen states, responsiveness, accessibility "
                "intent, and contract coverage; do not require React, event handlers, or a "
                "runnable application, which belong exclusively to EXECUTE."
            ),
            Stage.DECIDE: (
                "DECIDE is a minimal system and decision map. Assess components, connections, "
                "data, risks, and reproducible commands; do not require implemented code."
            ),
            Stage.SIMULATE: (
                "SIMULATE is an execution graph. Assess dependencies, ordering, measurable done "
                "conditions, and verification coverage; do not require implemented code."
            ),
            Stage.EXECUTE: (
                "This review covers the autonomous-change contract before workspace execution. "
                "The canonical normalized EXECUTE command manifest is authoritative. An exact "
                "legacy DECIDE compatibility command profile is deliberately normalized to that "
                "manifest; do not report the normalized commands as a mismatch or demand legacy "
                "keys or values. build_evidence is intentionally a placeholder. Do not report "
                "missing command "
                "results, package-script verification, browser receipts, or source/runtime "
                "defects. The deterministic runner and separate post-build workspace reviewer "
                "exclusively own those conditions."
            ),
            Stage.OBSERVE: (
                "OBSERVE owns reproducible proof, honest defects, and an executable demo plan. "
                "Assess evidence resolution and exact accessible actions against the built app. "
                "Proof status is a closed vocabulary: pass or passed means the cited evidence "
                "proves the item; failed means it does not. Never use verified. "
                "Mark the upstream producer unusable only for a concrete delivered-app or "
                "committed-evidence defect that requires EXECUTE to revise. Missing or malformed "
                "demo-plan content is an OBSERVE consumer defect and must be repaired here, not "
                "routed upstream. Do not require an input, validation flow, or other capability "
                "that the product contract and actual UI do not expose. "
                "The recorder supports only click, select, fill, scroll, and wait; observations "
                "must be positive waits, never navigate, inspect, or assert_* actions. A wait's "
                "value is numeric timing only; the exact observed text belongs in name. Roles "
                "must be real ARIA roles; plain text uses an empty role and CSS uses selector."
            ),
        }
        return boundaries[stage]

    def _recent_gate_findings(self, stage: Stage) -> list[dict[str, Any]]:
        """Carry failed-stage evidence into a resumed autonomous attempt.

        A resume starts a fresh pair pass, so without this bounded history the pair can
        repeat a defect whose repair budget is already exhausted. The history is
        read-only run evidence and does not alter rewards, vectors, or trust state.
        """

        rows = self.store.read_jsonl(self.store.logs_dir / "gates.jsonl")
        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in reversed(rows):
            if row.get("stage") != stage.value or row.get("decision") != "REPAIR":
                continue
            for finding in row.get("findings", []):
                if not isinstance(finding, dict):
                    continue
                key = (
                    str(finding.get("rule_id", "")),
                    str(finding.get("message", "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "rule_id": key[0],
                        "severity": str(finding.get("severity", "")),
                        "message": key[1],
                    }
                )
                if len(findings) >= 12:
                    return findings
        return findings

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
                    "relationship_vector_digest": self.relationship_digest,
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
            relationship_vector_digest=self.relationship_digest,
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

    def run_inference_night_workspace(
        self,
        *,
        shadow_updates: list[dict[str, Any]],
        handoffs: list[dict[str, Any]],
        proposed_memory_updates: dict[str, list[str]],
    ) -> InferenceNightOutput:
        curator = AgentIdentity(
            name="Inference Night Curator",
            personality=Personality.CONSCIENTIOUSNESS,
            team=Stage.OBSERVE,
        )
        invocation = AgentInvocation(
            invocation_id=self._id(Stage.OBSERVE, curator.name, "inference-night", 1),
            run_id=self.store.run_id,
            phase="delivery",
            stage="NIGHT_COMMIT",
            pair="SYSTEM",
            agent=curator,
            mode=ExecutionMode.CURATION,
            objective=(
                "In one workspace pass, audit the accumulated handoff rewards and shadow "
                "trust deltas, then consolidate each agent's persistent memory. Trust math "
                "is deterministic: approve it only when its evidence is internally consistent. "
                f"For every agent, retain at least one proposed evidence-backed memory carrying "
                f"the exact token [run:{self.store.run_id}]. Memory is the fast learning variable; "
                "do not replace new run-specific evidence with generic stage summaries."
            ),
            inputs={
                "purpose": "inference_night_commit",
                "shadow_updates": shadow_updates,
                "handoffs": handoffs,
                "current_memories": self.memories,
                "proposed_memory_updates": proposed_memory_updates,
                "evidence": [
                    f"logs/handoff_rewards.canonical.jsonl#{index + 1}"
                    for index in range(len(handoffs))
                ],
            },
            relationship_vector_digest=self.relationship_digest,
            workspace_root=str(self.store.workspace_dir),
            permission_profile="read-only",
            output_schema=inference_night_schema(sorted(self.memories)),
            model=self.settings.models["night"]["model"],
            reasoning_effort=self.settings.models["night"]["reasoning_effort"],
            timeout_seconds=int(self.settings.training["timeout_seconds"]),
            retry_budget=int(self.settings.delivery["backend_retries_per_call"]),
        )
        result = self._invoke(invocation)
        if result.status != InvocationStatus.COMPLETED or result.output is None:
            raise RuntimeError("inference night workspace failed")
        output = InferenceNightOutput.model_validate(result.output)
        if set(output.memory_updates) != set(self.memories):
            raise RuntimeError(
                "inference night workspace must return memory state for all 14 agents"
            )
        run_token = f"[run:{self.store.run_id}]"
        missing_run_evidence = [
            agent
            for agent, lines in output.memory_updates.items()
            if not any(run_token in line for line in lines)
        ]
        if missing_run_evidence:
            raise RuntimeError(
                "inference night workspace dropped new evidence-backed memory for: "
                + ", ".join(sorted(missing_run_evidence))
            )
        return output

    def _upstream_guidance(
        self,
        agent: AgentIdentity,
        upstream_pair: tuple[str, str] | None,
    ) -> list[dict[str, str]]:
        if upstream_pair is None:
            return []
        agents = self.settings.agents
        return [
            {
                "producer": producer,
                "stance": qualitative_stance_for(
                    agent,
                    agents[producer],
                    self.relationships,
                ),
            }
            for producer in upstream_pair
        ]

    def _id(self, stage: Stage, agent: str, operation: str, attempt: int) -> str:
        suffix = uuid.uuid4().hex[:6]
        return f"{self.store.run_id}-{stage.value}-{agent}-{operation}-{attempt}-{suffix}"
