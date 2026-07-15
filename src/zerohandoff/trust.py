from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from zerohandoff.config import digest_value
from zerohandoff.models import (
    AgentIdentity,
    DirectedRelationship,
    FrozenRelationshipSnapshot,
    Personality,
    RelationshipPolicy,
    RelationshipVector,
    TeamDefinition,
)


RELATIONSHIP_GUIDANCE = {
    "respect": {
        "positive": "Take your partner's critique seriously and respond to its substance.",
        "negative": (
            "Do not dismiss your partner; require concrete evidence and answer it directly."
        ),
    },
    "reliability": {
        "positive": "Treat routine partner claims as reliable while preserving mandatory checks.",
        "negative": "Independently verify your partner's material claims before adopting them.",
    },
    "warmth": {
        "positive": "Use a supportive, constructive tone with your partner.",
        "negative": "Keep the exchange neutral, professional, and free of unnecessary friction.",
    },
    "patience": {
        "positive": "Explain your reasoning and work through ambiguity patiently.",
        "negative": (
            "Keep the exchange concise and request structured evidence when clarification "
            "is needed."
        ),
    },
    "resentment": {
        "positive": (
            "Address unresolved friction explicitly, neutrally, and through evidence "
            "rather than hostility."
        ),
        "negative": "Approach the partner without carrying prior friction into this decision.",
    },
    "dominance": {
        "positive": (
            "Let your partner propose an initial direction while retaining independent "
            "judgment."
        ),
        "negative": "Take initiative on the first proposal and explicitly invite partner review.",
    },
    "openness": {
        "positive": "Remain willing to revise your judgment when your partner supplies evidence.",
        "negative": "Require concrete evidence before revising, while staying open to correction.",
    },
    "confidence": {
        "positive": (
            "Give weight to your partner's domain judgment without bypassing quality gates."
        ),
        "negative": "Independently validate your partner's conclusions before relying on them.",
    },
    "affection": {
        "positive": "Invest effort in helping your partner reach a strong shared result.",
        "negative": "Keep collaboration task-focused, fair, and professionally cooperative.",
    },
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def stance_band(trust: float) -> str:
    if trust >= 0.6:
        return "Strongly trust"
    if trust >= 0.2:
        return "Trust"
    if trust > -0.2:
        return "Neutral"
    if trust > -0.6:
        return "Skeptical"
    return "Distrust"


def initialize_relationships(teams: Iterable[TeamDefinition]) -> dict[str, DirectedRelationship]:
    relationships: dict[str, DirectedRelationship] = {}
    for team in teams:
        left, right = team.agents
        for source, target in ((left, right), (right, left)):
            relationship = DirectedRelationship(source=source.name, target=target.name)
            relationships[relationship.edge_id] = relationship
    return relationships


@dataclass(frozen=True)
class LeadDecision:
    lead: AgentIdentity
    peer: AgentIdentity
    reason: str
    inbound_trust: dict[str, float]


def select_lead(
    team: TeamDefinition,
    relationships: dict[str, DirectedRelationship],
    *,
    last_speaker: str,
) -> LeadDecision:
    left, right = team.agents
    inbound = {
        left.name: relationships[f"{right.name}->{left.name}"].vector.trust,
        right.name: relationships[f"{left.name}->{right.name}"].vector.trust,
    }
    if inbound[left.name] > inbound[right.name]:
        return LeadDecision(left, right, "higher_inbound_trust", inbound)
    if inbound[right.name] > inbound[left.name]:
        return LeadDecision(right, left, "higher_inbound_trust", inbound)
    left_dominant = left.personality == Personality.DOMINANCE
    right_dominant = right.personality == Personality.DOMINANCE
    if left_dominant and not right_dominant:
        return LeadDecision(left, right, "personality_dominance_tiebreak", inbound)
    if right_dominant and not left_dominant:
        return LeadDecision(right, left, "personality_dominance_tiebreak", inbound)
    if last_speaker == left.name:
        return LeadDecision(left, right, "last_speaker_tiebreak", inbound)
    if last_speaker == right.name:
        return LeadDecision(right, left, "last_speaker_tiebreak", inbound)
    raise ValueError("last_speaker must be a member of the team")


def update_trust_for_episode(
    relationships: dict[str, DirectedRelationship],
    team: TeamDefinition,
    *,
    reward: int,
    expected_success: float,
    agreement: bool,
    selected_lead: str,
    alpha: float = 0.2,
    step_max: float = 0.25,
) -> list[dict[str, float | str]]:
    prediction_error = reward - expected_success
    changes: list[dict[str, float | str]] = []
    left, right = team.agents
    for source, target in ((left, right), (right, left)):
        edge_id = f"{source.name}->{target.name}"
        relationship = relationships[edge_id]
        if agreement:
            credit = 0.5
        else:
            credit = 1.0 if target.name == selected_lead else 0.0
        delta = clamp(alpha * prediction_error * credit, -step_max, step_max)
        before = relationship.vector.trust
        after = clamp(before + delta, -1.0, 1.0)
        relationship.vector.trust = after
        changes.append(
            {
                "edge_id": edge_id,
                "result": float(reward),
                "expected_success": expected_success,
                "prediction_error": prediction_error,
                "credit": credit,
                "trust_before": before,
                "trust_delta": delta,
                "trust_after": after,
            }
        )
    return changes


def update_difficulty_expectation(current: float, rewards: list[int], beta: float = 0.1) -> float:
    if not rewards:
        return current
    mean_reward = sum(rewards) / len(rewards)
    return clamp(current + beta * (mean_reward - current), 0.0, 1.0)


def freeze_relationships(
    training_run_id: str,
    relationships: dict[str, DirectedRelationship],
) -> FrozenRelationshipSnapshot:
    vector_payload = {
        edge_id: relationship.model_dump(mode="json")
        for edge_id, relationship in sorted(relationships.items())
    }
    digest = digest_value(
        {
            "schema_version": "1.0",
            "training_run_id": training_run_id,
            "frozen": True,
            "vectors": vector_payload,
        }
    )
    return FrozenRelationshipSnapshot(
        training_run_id=training_run_id,
        vectors=relationships,
        content_digest=digest,
    )


def verify_frozen_snapshot(snapshot: FrozenRelationshipSnapshot) -> bool:
    payload = {
        "schema_version": snapshot.schema_version,
        "training_run_id": snapshot.training_run_id,
        "frozen": True,
        "vectors": {
            edge_id: relationship.model_dump(mode="json")
            for edge_id, relationship in sorted(snapshot.vectors.items())
        },
    }
    return snapshot.frozen and digest_value(payload) == snapshot.content_digest


def qualitative_stance_for(
    source: AgentIdentity,
    target: AgentIdentity,
    relationships: dict[str, DirectedRelationship],
) -> str:
    return stance_band(relationships[f"{source.name}->{target.name}"].vector.trust)


def compile_relationship_policy(
    source: AgentIdentity,
    target: AgentIdentity,
    relationships: dict[str, DirectedRelationship],
    settings: dict[str, object],
) -> RelationshipPolicy:
    relationship = relationships[f"{source.name}->{target.name}"]
    vector = relationship.vector.model_dump()
    version = str(settings["compiler_version"])
    if not bool(settings.get("enabled", True)):
        return RelationshipPolicy(
            compiler_version=version,
            source=source.name,
            target=target.name,
            source_vector_digest=digest_value(vector),
            selected_dimensions=[],
            instructions=[],
            rendered="No non-trust relationship guidance is active.",
        )

    threshold = float(settings["activation_threshold"])
    maximum = int(settings["max_dimensions"])
    raw_priority = settings["dimension_priority"]
    if not isinstance(raw_priority, list):
        raise ValueError("relationship policy dimension_priority must be a list")
    priority = [str(value) for value in raw_priority]
    if not 1 <= maximum <= len(RELATIONSHIP_GUIDANCE):
        raise ValueError("relationship policy max_dimensions is out of range")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("relationship policy activation_threshold is out of range")
    if set(priority) != set(RELATIONSHIP_GUIDANCE):
        raise ValueError("relationship policy priority must contain all nine non-trust dimensions")
    rank = {dimension: index for index, dimension in enumerate(priority)}
    candidates = [
        (dimension, float(vector[dimension]))
        for dimension in RELATIONSHIP_GUIDANCE
        if abs(float(vector[dimension])) >= threshold
    ]
    candidates.sort(key=lambda item: (-abs(item[1]), rank[item[0]], item[0]))
    selected = candidates[:maximum]
    instructions = [
        RELATIONSHIP_GUIDANCE[dimension]["positive" if value >= 0 else "negative"]
        for dimension, value in selected
    ]
    selected_dimensions = [
        f"{dimension}:{'positive' if value >= 0 else 'negative'}"
        for dimension, value in selected
    ]
    rendered = (
        " ".join(instructions)
        if instructions
        else "Use a neutral, professional collaboration style with your partner."
    )
    return RelationshipPolicy(
        compiler_version=version,
        source=source.name,
        target=target.name,
        source_vector_digest=digest_value(vector),
        selected_dimensions=selected_dimensions,
        instructions=instructions,
        rendered=rendered,
    )


def apply_curator_deltas(
    relationship: DirectedRelationship,
    deltas: dict[str, float],
) -> DirectedRelationship:
    updated = relationship.model_copy(deep=True)
    updated.vector = relationship.vector.apply_non_trust_deltas(deltas)
    return updated
