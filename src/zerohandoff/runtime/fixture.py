from __future__ import annotations

import time
from typing import Any

from jsonschema import ValidationError, validate

from zerohandoff.models import AgentInvocation, AgentResult, InvocationStatus, NON_TRUST_DIMENSIONS
from zerohandoff.runtime.base import RuntimeCapabilities


class FixtureAdapter:
    """Deterministic, agent-free adapter used by tests and smoke execution."""

    name = "fixture"

    def __init__(self, overrides: dict[str, dict[str, Any]] | None = None) -> None:
        self.overrides = overrides or {}
        self.calls: list[AgentInvocation] = []
        self.cancelled: set[str] = set()

    def probe(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            available=True,
            prompt_execution=True,
            structured_output=True,
            workspace_write=True,
            json_events=True,
            cancellation=True,
            version="fixture-1",
        )

    def cancel(self, invocation_id: str) -> bool:
        self.cancelled.add(invocation_id)
        return True

    def cancel_all(self) -> int:
        return 0

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        started = time.monotonic()
        self.calls.append(invocation)
        if invocation.invocation_id in self.cancelled:
            return AgentResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.CANCELLED,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        override = self.overrides.get(invocation.invocation_id, {})
        forced_status = override.get("status") or invocation.inputs.get("fixture_status")
        if forced_status:
            status = InvocationStatus(forced_status)
            return AgentResult(
                invocation_id=invocation.invocation_id,
                status=status,
                output=override.get("output"),
                error={"code": f"fixture_{status.value}", "message": "forced fixture result"}
                if status != InvocationStatus.COMPLETED
                else None,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        output = override.get("output") or invocation.fixture_data.get("output")
        if output is None:
            output = self._default_output(invocation)
        if invocation.output_schema is not None:
            try:
                validate(instance=output, schema=invocation.output_schema)
            except ValidationError as exc:
                return AgentResult(
                    invocation_id=invocation.invocation_id,
                    status=InvocationStatus.INVALID_OUTPUT,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error={"code": "fixture_invalid_output", "message": str(exc)},
                )
        return AgentResult(
            invocation_id=invocation.invocation_id,
            status=InvocationStatus.COMPLETED,
            output=output,
            events=[{"type": "fixture.completed", "invocation_id": invocation.invocation_id}],
            duration_ms=int((time.monotonic() - started) * 1000),
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    def _default_output(self, invocation: AgentInvocation) -> dict[str, Any]:
        purpose = invocation.inputs.get("purpose")
        if purpose in {"training_initial", "training_revised"}:
            answer = invocation.fixture_data.get("answer", "")
            return {
                "answer": str(answer),
                "reasoning": f"Fixture reasoning from {invocation.agent.name}.",
                "memory_ids_used": [],
                "diary_evidence": f"Worked on {invocation.inputs.get('puzzle_id', 'fixture puzzle')}.",
            }
        if purpose == "night_curator":
            reward = int(invocation.inputs.get("reward", 0))
            delta = 0.01 if reward else -0.01
            return {
                "training_agent": invocation.agent.name,
                "theme_map": [
                    {
                        "theme": "round",
                        "evidence": [str(invocation.inputs.get("round_id", 0))],
                    }
                ],
                "relationship_deltas": {
                    dimension: (-delta if dimension == "resentment" else delta)
                    for dimension in sorted(NON_TRUST_DIMENSIONS)
                },
                "retained_memory_ids": invocation.inputs.get("memory_ids", []),
                "compressed_memories": [],
                "forgotten_memory_ids": [],
                "blocking_issues": [],
                "resolved_issues": [],
                "decision_reasons": ["Deterministic fixture consolidation."],
                "final_memory_lines": [
                    f"Round {invocation.inputs.get('round_id', 0)} reward was {reward}."
                ],
                "evidence": invocation.inputs.get("episode_ids", []),
                "confidence": 1.0,
            }
        return {
            "summary": f"Fixture output for {invocation.stage} by {invocation.agent.name}",
            "content": invocation.objective,
        }
