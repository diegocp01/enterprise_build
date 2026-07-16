from __future__ import annotations

import json

import pytest

from zerohandoff.continual import (
    InferenceLearningStore,
    initialize_inference_state,
    verify_inference_state,
)
from zerohandoff.models import InferenceNightOutput
from zerohandoff.runtime.fixture import FixtureAdapter
from zerohandoff.training.orchestrator import TrainingOrchestrator
from zerohandoff.training.puzzles import PuzzleRepository


def _frozen(tmp_path, repo_root, settings):
    return TrainingOrchestrator(
        settings=settings,
        adapter=FixtureAdapter(),
        puzzle_repository=PuzzleRepository.load(repo_root / "data" / "puzzles.fixture.json"),
        base_dir=tmp_path / "training",
    ).run(rounds=1).frozen_snapshot


def test_inference_copy_keeps_training_edges_and_zero_initializes_cross_team(
    tmp_path, repo_root, settings
) -> None:
    frozen = _frozen(tmp_path, repo_root, settings)
    assert frozen is not None
    frozen_payload = frozen.model_dump(mode="json")
    state = initialize_inference_state(
        frozen,
        settings.agents,
        updates_enabled=True,
        expected_success_initial=0.5,
    )
    assert verify_inference_state(state)
    assert len(state.vectors) == 14 * 13
    for edge_id, trained in frozen.vectors.items():
        assert state.vectors[edge_id].vector == trained.vector
    cross = next(
        edge
        for edge in state.vectors.values()
        if settings.agents[edge.source].team != settings.agents[edge.target].team
    )
    assert set(cross.vector.model_dump().values()) == {0.0}
    assert frozen.model_dump(mode="json") == frozen_payload


def test_commit_is_conflict_checked_and_never_rewrites_frozen_snapshot(
    tmp_path, repo_root, settings
) -> None:
    frozen = _frozen(tmp_path, repo_root, settings)
    assert frozen is not None
    baseline = json.dumps(frozen.model_dump(mode="json"), sort_keys=True)
    store = InferenceLearningStore(tmp_path / "learning")
    start = store.load_or_initialize(
        frozen, settings.agents, settings.continual_learning
    )
    shadow = start.model_copy(deep=True)
    night = InferenceNightOutput(
        trust_commit_approved=True,
        memory_updates=shadow.memories,
        blocking_issues=[],
        resolved_issues=[],
        decision_reasons=["fixture"],
        evidence=[],
        confidence=1.0,
    )
    committed = store.commit(
        run_id="prototype_one",
        start_state=start,
        shadow_state=shadow,
        night=night,
    )
    assert committed.commit_sequence == 1
    with pytest.raises(RuntimeError, match="commit conflict"):
        store.commit(
            run_id="stale_prototype",
            start_state=start,
            shadow_state=shadow,
            night=night,
        )
    assert json.dumps(frozen.model_dump(mode="json"), sort_keys=True) == baseline


def test_commit_rejects_any_non_trust_dimension_change(
    tmp_path, repo_root, settings
) -> None:
    frozen = _frozen(tmp_path, repo_root, settings)
    assert frozen is not None
    store = InferenceLearningStore(tmp_path / "learning")
    start = store.load_or_initialize(
        frozen, settings.agents, settings.continual_learning
    )
    shadow = start.model_copy(deep=True)
    next(iter(shadow.vectors.values())).vector.respect = 0.1
    night = InferenceNightOutput(
        trust_commit_approved=True,
        memory_updates=shadow.memories,
        blocking_issues=[],
        resolved_issues=[],
        decision_reasons=["fixture"],
        evidence=[],
        confidence=1.0,
    )
    with pytest.raises(ValueError, match="non-trust dimensions"):
        store.commit(
            run_id="invalid_non_trust_change",
            start_state=start,
            shadow_state=shadow,
            night=night,
        )


def test_latest_invalid_commit_can_be_preserved_and_restored(
    tmp_path, repo_root, settings
) -> None:
    frozen = _frozen(tmp_path, repo_root, settings)
    assert frozen is not None
    store = InferenceLearningStore(tmp_path / "learning")
    start = store.load_or_initialize(
        frozen, settings.agents, settings.continual_learning
    )
    shadow = start.model_copy(deep=True)
    night = InferenceNightOutput(
        trust_commit_approved=True,
        memory_updates=shadow.memories,
        blocking_issues=[],
        resolved_issues=[],
        decision_reasons=["fixture"],
        evidence=[],
        confidence=1.0,
    )
    committed = store.commit(
        run_id="invalidated_prototype",
        start_state=start,
        shadow_state=shadow,
        night=night,
    )
    record = store.invalidate_latest_commit(
        run_id="invalidated_prototype",
        start_state=start,
        invalid_end_digest=committed.content_digest,
        reason="duplicate resume rewards",
    )
    restored = store.load_or_initialize(
        frozen, settings.agents, settings.continual_learning
    )
    assert restored.content_digest == start.content_digest
    assert record["record_type"] == "commit_invalidation"
    assert (store.root / record["preserved_state"]).is_file()
