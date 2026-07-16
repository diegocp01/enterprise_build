from __future__ import annotations

import json
from pathlib import Path

from zerohandoff.models import (
    AgentInvocation,
    AgentResult,
    ArtifactEnvelope,
    BuildRequest,
    DirectedRelationship,
    EpisodeRecord,
    EventRecord,
    FrozenRelationshipSnapshot,
    GateResult,
    HandoffAssessment,
    InferenceLearningState,
    InferenceNightOutput,
    NightCuratorOutput,
    RelationshipPolicy,
    RelationshipVector,
    RepairPacket,
    RunManifest,
    ShadowTrustUpdate,
)


SCHEMA_MODELS = {
    model.__name__: model
    for model in (
        AgentInvocation,
        AgentResult,
        ArtifactEnvelope,
        BuildRequest,
        DirectedRelationship,
        EpisodeRecord,
        EventRecord,
        FrozenRelationshipSnapshot,
        GateResult,
        HandoffAssessment,
        InferenceLearningState,
        InferenceNightOutput,
        NightCuratorOutput,
        RelationshipPolicy,
        RelationshipVector,
        RepairPacket,
        RunManifest,
        ShadowTrustUpdate,
    )
}


def export_schemas(destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, model in sorted(SCHEMA_MODELS.items()):
        path = destination / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n")
        paths.append(path)
    return paths
