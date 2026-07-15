from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from zerohandoff.models import AgentInvocation, AgentResult


class RuntimeCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    prompt_execution: bool = False
    structured_output: bool = False
    workspace_write: bool = False
    json_events: bool = False
    cancellation: bool = False
    version: str | None = None
    details: dict[str, str] = {}


class RuntimeAdapter(Protocol):
    name: str

    def probe(self) -> RuntimeCapabilities: ...

    def invoke(self, invocation: AgentInvocation) -> AgentResult: ...

    def cancel(self, invocation_id: str) -> bool: ...

