from __future__ import annotations

from zerohandoff.models import Personality, Stage
from zerohandoff.trust import (
    compile_relationship_policy,
    initialize_relationships,
    select_lead,
    update_trust_for_episode,
)


def test_lead_resolution_covers_every_authoritative_tie_branch(settings) -> None:
    seek = settings.teams[Stage.SENSE]
    relationships = initialize_relationships(settings.teams.values())
    mira, zephyr = seek.agents

    relationships[f"{zephyr.name}->{mira.name}"].vector.trust = 0.8
    relationships[f"{mira.name}->{zephyr.name}"].vector.trust = 0.1
    decision = select_lead(seek, relationships, last_speaker=mira.name)
    assert decision.lead == mira
    assert decision.reason == "higher_inbound_trust"

    relationships[f"{zephyr.name}->{mira.name}"].vector.trust = 0.0
    relationships[f"{mira.name}->{zephyr.name}"].vector.trust = 0.0
    decision = select_lead(seek, relationships, last_speaker=mira.name)
    assert zephyr.personality == Personality.DOMINANCE
    assert decision.lead == zephyr
    assert decision.reason == "personality_dominance_tiebreak"

    shape = settings.teams[Stage.MODEL]
    kestrel, rowan = shape.agents
    decision = select_lead(shape, relationships, last_speaker=rowan.name)
    assert decision.lead == rowan
    assert decision.reason == "last_speaker_tiebreak"
    assert decision.reason not in {"alphabetical", "stable_hash", "agent_id"}


def test_directed_trust_edges_update_independently(settings) -> None:
    team = settings.teams[Stage.SENSE]
    relationships = initialize_relationships(settings.teams.values())
    left, right = team.agents
    changes = update_trust_for_episode(
        relationships,
        team,
        reward=1,
        expected_success=0.5,
        agreement=False,
        selected_lead=left.name,
    )
    assert len(changes) == 2
    assert relationships[f"{right.name}->{left.name}"].vector.trust == 0.1
    assert relationships[f"{left.name}->{right.name}"].vector.trust == 0.0


def test_relationship_policy_is_deterministic_bounded_and_never_changes_authority(
    settings,
) -> None:
    team = settings.teams[Stage.SENSE]
    source, target = team.agents
    relationships = initialize_relationships(settings.teams.values())
    edge = relationships[f"{source.name}->{target.name}"]
    edge.vector.reliability = -0.8
    edge.vector.openness = 0.7
    edge.vector.respect = 0.6
    edge.vector.affection = 0.9
    edge.vector.trust = -0.4

    first = compile_relationship_policy(
        source,
        target,
        relationships,
        settings.relationship_policy,
    )
    second = compile_relationship_policy(
        source,
        target,
        relationships,
        settings.relationship_policy,
    )
    assert first == second
    assert first.selected_dimensions == [
        "affection:positive",
        "reliability:negative",
        "openness:positive",
    ]
    assert len(first.instructions) == 3
    assert not any(str(value) in first.rendered for value in (-0.8, 0.7, 0.9))

    decision_before = select_lead(team, relationships, last_speaker=target.name)
    edge.vector.affection = -0.9
    edge.vector.reliability = 0.9
    decision_after = select_lead(team, relationships, last_speaker=target.name)
    assert decision_before.lead == decision_after.lead
    assert decision_before.reason == decision_after.reason


def test_zero_relationship_vector_compiles_to_neutral_guidance(settings) -> None:
    team = settings.teams[Stage.SENSE]
    source, target = team.agents
    relationships = initialize_relationships(settings.teams.values())
    policy = compile_relationship_policy(
        source,
        target,
        relationships,
        settings.relationship_policy,
    )
    assert policy.selected_dimensions == []
    assert policy.instructions == []
    assert "neutral" in policy.rendered
