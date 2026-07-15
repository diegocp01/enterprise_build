from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Stage(StrEnum):
    INTAKE = "INTAKE"
    SENSE = "SENSE"
    MODEL = "MODEL"
    COMPOSE = "COMPOSE"
    DECIDE = "DECIDE"
    SIMULATE = "SIMULATE"
    EXECUTE = "EXECUTE"
    OBSERVE = "OBSERVE"
    DEMO = "DEMO"
    BUNDLE = "BUNDLE"


DELIVERY_STAGES = (
    Stage.SENSE,
    Stage.MODEL,
    Stage.COMPOSE,
    Stage.DECIDE,
    Stage.SIMULATE,
    Stage.EXECUTE,
    Stage.OBSERVE,
)


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INFRASTRUCTURE_FAILED = "infrastructure_failed"


class InvocationStatus(StrEnum):
    COMPLETED = "completed"
    INVALID_OUTPUT = "invalid_output"
    TIMEOUT = "timeout"
    BACKEND_ERROR = "backend_error"
    CANCELLED = "cancelled"


class EpisodeStatus(StrEnum):
    COMPLETED = "completed"
    AGENT_TIMEOUT = "agent_timeout"
    INVALID_AGENT_OUTPUT = "invalid_agent_output"
    BACKEND_ERROR = "backend_error"


class GateDecision(StrEnum):
    PASS = "PASS"
    REPAIR = "REPAIR"
    BLOCKED = "BLOCKED"
    INFRA_ERROR = "INFRA_ERROR"


class ExecutionMode(StrEnum):
    ARTIFACT = "artifact"
    WORKSPACE = "workspace"
    REVIEW = "review"
    TRAINING = "training"
    CURATION = "curation"


class Personality(StrEnum):
    DOMINANCE = "Dominance"
    INFLUENCE = "Influence"
    STEADINESS = "Steadiness"
    CONSCIENTIOUSNESS = "Conscientiousness"


class AgentIdentity(Contract):
    name: str = Field(min_length=1)
    personality: Personality
    team: Stage


class TeamDefinition(Contract):
    stage: Stage
    agents: tuple[AgentIdentity, AgentIdentity]

    @model_validator(mode="after")
    def agents_belong_to_team(self) -> TeamDefinition:
        if any(agent.team != self.stage for agent in self.agents):
            raise ValueError("every agent must belong to the declared team")
        if self.agents[0].name == self.agents[1].name:
            raise ValueError("team agents must be distinct")
        return self


class RelationshipVector(Contract):
    trust: float = Field(default=0.0, ge=-1.0, le=1.0)
    respect: float = Field(default=0.0, ge=-1.0, le=1.0)
    reliability: float = Field(default=0.0, ge=-1.0, le=1.0)
    warmth: float = Field(default=0.0, ge=-1.0, le=1.0)
    patience: float = Field(default=0.0, ge=-1.0, le=1.0)
    resentment: float = Field(default=0.0, ge=-1.0, le=1.0)
    dominance: float = Field(default=0.0, ge=-1.0, le=1.0)
    openness: float = Field(default=0.0, ge=-1.0, le=1.0)
    confidence: float = Field(default=0.0, ge=-1.0, le=1.0)
    affection: float = Field(default=0.0, ge=-1.0, le=1.0)

    def apply_non_trust_deltas(self, deltas: dict[str, float]) -> RelationshipVector:
        values = self.model_dump()
        for dimension, delta in deltas.items():
            if dimension == "trust" or dimension not in values:
                raise ValueError(f"invalid curator relationship dimension: {dimension}")
            if abs(delta) > 0.2:
                raise ValueError(f"delta for {dimension} exceeds 0.2")
            values[dimension] = max(-1.0, min(1.0, values[dimension] + delta))
        return RelationshipVector.model_validate(values)


class DirectedRelationship(Contract):
    source: str
    target: str
    vector: RelationshipVector = Field(default_factory=RelationshipVector)

    @property
    def edge_id(self) -> str:
        return f"{self.source}->{self.target}"


class FrozenRelationshipSnapshot(Contract):
    schema_version: str = "1.0"
    training_run_id: str
    frozen_at: datetime = Field(default_factory=utc_now)
    frozen: Literal[True] = True
    vectors: dict[str, DirectedRelationship]
    content_digest: str


class BuildRequest(Contract):
    idea: str = Field(min_length=3)
    audience: str = Field(min_length=2)
    outcome: str = Field(min_length=3)
    constraints: list[str] = Field(default_factory=list)
    must_have_capabilities: list[str] = Field(default_factory=list)
    existing_assets: list[str] = Field(default_factory=list)
    visual_direction: str | None = None
    out_of_scope: list[str] = Field(default_factory=list)
    demo_preferences: str | None = None

    @field_validator("constraints", "must_have_capabilities", "out_of_scope")
    @classmethod
    def strip_empty_items(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class ArtifactEnvelope(Contract):
    schema_version: str = "1.1"
    artifact_id: str
    artifact_type: str
    version: int = Field(ge=1)
    run_id: str
    stage: Stage
    producer_pair: str
    lead: str
    peer: str
    input_digests: dict[str, str] = Field(default_factory=dict)
    content_files: list[str]
    content_digest: str
    contract_item_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("contract_item_ids", "requirement_ids"),
    )
    gate_status: GateDecision
    created_at: datetime = Field(default_factory=utc_now)


class EventRecord(Contract):
    schema_version: str = "1.0"
    event_id: str
    sequence: int = Field(ge=1)
    run_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    event_type: str
    phase: Literal["training", "delivery", "system"]
    stage: str | None = None
    attempt: int | None = None
    actor: str | None = None
    adapter: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    relationship_vector_digest: str | None = None
    settings_digest: str | None = None
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    status: str
    duration_ms: int | None = Field(default=None, ge=0)
    usage: dict[str, Any] | None = None
    git_commit: str | None = None
    error: dict[str, Any] | None = None
    redactions: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class RelationshipPolicy(Contract):
    schema_version: str = "1.0"
    compiler_version: str
    source: str
    target: str
    source_vector_digest: str
    selected_dimensions: list[str]
    instructions: list[str]
    rendered: str


class AgentInvocation(Contract):
    invocation_id: str
    run_id: str
    phase: Literal["training", "delivery"]
    stage: str
    pair: str
    agent: AgentIdentity
    attempt: int = Field(default=1, ge=1)
    mode: ExecutionMode
    objective: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    # Deterministic test data is never serialized into prompts or evidence sent to a live host.
    fixture_data: dict[str, Any] = Field(default_factory=dict, exclude=True)
    input_digests: dict[str, str] = Field(default_factory=dict)
    relationship_vector_digest: str | None = None
    trust_stance: str | None = None
    relationship_policy: RelationshipPolicy | None = None
    workspace_root: str | None = None
    permission_profile: Literal["read-only", "workspace-write"] = "read-only"
    output_schema: dict[str, Any] | None = None
    model: str
    reasoning_effort: str
    timeout_seconds: int = Field(default=180, ge=1)
    retry_budget: int = Field(default=1, ge=0)


class AgentResult(Contract):
    invocation_id: str
    status: InvocationStatus
    output: dict[str, Any] | None = None
    produced_files: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    raw_trace: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    usage: dict[str, Any] | None = None
    resume_token: str | None = None
    error: dict[str, Any] | None = None


class ReviewFinding(Contract):
    rule_id: str
    severity: Literal["critical", "high", "medium", "low"]
    message: str
    evidence: list[str] = Field(default_factory=list)


class GateResult(Contract):
    stage: Stage
    decision: GateDecision
    rule_results: dict[str, bool]
    findings: list[ReviewFinding] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class RepairPacket(Contract):
    repair_id: str
    run_id: str
    stage: Stage
    attempt: int = Field(ge=1)
    remaining_budget: int = Field(ge=0)
    findings: list[ReviewFinding]
    artifact_refs: list[str]
    expected_condition: str


class EpisodeMessage(Contract):
    agent: str
    turn: Literal["initial", "revised"]
    answer: str
    reasoning: str


class EpisodeRecord(Contract):
    run_id: str
    round_id: int = Field(ge=1)
    team_id: str
    episode_id: str
    puzzle_id: str
    puzzle_digest: str
    difficulty: str
    status: EpisodeStatus
    messages: list[EpisodeMessage]
    initial_answers: dict[str, str]
    final_answers: dict[str, str]
    final_answer: str | None
    selected_lead: str | None
    resolution_reason: str | None
    reward: int | None = Field(default=None, ge=0, le=1)
    agreement: bool
    productive_disagreement: bool
    last_speaker: str
    memory_ids_used: dict[str, list[str]] = Field(default_factory=dict)
    diary_evidence: dict[str, str] = Field(default_factory=dict)
    duration_ms: int = Field(default=0, ge=0)


NON_TRUST_DIMENSIONS = {
    "respect",
    "reliability",
    "warmth",
    "patience",
    "resentment",
    "dominance",
    "openness",
    "confidence",
    "affection",
}


class ThemeMapEntry(Contract):
    theme: str
    evidence: list[str]


class RelationshipDeltas(Contract):
    respect: float = Field(ge=-0.2, le=0.2)
    reliability: float = Field(ge=-0.2, le=0.2)
    warmth: float = Field(ge=-0.2, le=0.2)
    patience: float = Field(ge=-0.2, le=0.2)
    resentment: float = Field(ge=-0.2, le=0.2)
    dominance: float = Field(ge=-0.2, le=0.2)
    openness: float = Field(ge=-0.2, le=0.2)
    confidence: float = Field(ge=-0.2, le=0.2)
    affection: float = Field(ge=-0.2, le=0.2)


class NightCuratorOutput(Contract):
    training_agent: str
    theme_map: list[ThemeMapEntry]
    relationship_deltas: RelationshipDeltas
    retained_memory_ids: list[str]
    compressed_memories: list[str]
    forgotten_memory_ids: list[str]
    blocking_issues: list[str]
    resolved_issues: list[str]
    decision_reasons: list[str]
    final_memory_lines: list[str] = Field(max_length=5)
    evidence: list[str]
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_curator_contract(self) -> NightCuratorOutput:
        if self.blocking_issues and not self.resolved_issues:
            raise ValueError("blocking issues must be resolved in the same response")
        if sum(len(line.split()) for line in self.final_memory_lines) > 100:
            raise ValueError("final memory lines exceed 100 words")
        return self


class RunManifest(Contract):
    schema_version: str = "1.0"
    run_id: str
    run_type: Literal["training", "delivery"]
    status: RunStatus
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    settings_digest: str
    relationship_vector_digest: str | None = None
    adapter: str
    model_settings: dict[str, Any]
    stage_outcomes: dict[str, str] = Field(default_factory=dict)
    repair_counts: dict[str, int] = Field(default_factory=dict)
    artifact_checksums: dict[str, str] = Field(default_factory=dict)
    final_outcome: str | None = None
    failure_reason: str | None = None
