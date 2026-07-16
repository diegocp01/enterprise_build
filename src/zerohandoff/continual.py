from __future__ import annotations

import json
import os
import shutil
import uuid
import fcntl
from pathlib import Path
from typing import Iterable

from zerohandoff.config import digest_value
from zerohandoff.models import (
    DirectedRelationship,
    FrozenRelationshipSnapshot,
    HandoffAssessment,
    InferenceLearningState,
    InferenceNightOutput,
    RelationshipVector,
    ShadowTrustUpdate,
    Stage,
    utc_now,
)


def _state_payload(state: InferenceLearningState) -> dict:
    payload = state.model_dump(mode="json")
    payload.pop("content_digest", None)
    return payload


def sign_state(state: InferenceLearningState) -> InferenceLearningState:
    state.content_digest = digest_value(_state_payload(state))
    return state


def verify_inference_state(state: InferenceLearningState) -> bool:
    return state.content_digest == digest_value(_state_payload(state))


def initialize_inference_state(
    frozen: FrozenRelationshipSnapshot,
    agent_names: Iterable[str],
    *,
    updates_enabled: bool,
    expected_success_initial: float,
) -> InferenceLearningState:
    """Create the mutable inference copy while preserving the frozen source verbatim."""

    names = sorted(set(agent_names))
    vectors: dict[str, DirectedRelationship] = {}
    baseline_trust: dict[str, float] = {}
    for source in names:
        for target in names:
            if source == target:
                continue
            edge_id = f"{source}->{target}"
            trained = frozen.vectors.get(edge_id)
            vector = (
                trained.vector.model_copy(deep=True)
                if trained is not None
                else RelationshipVector()
            )
            vectors[edge_id] = DirectedRelationship(
                source=source,
                target=target,
                vector=vector,
            )
            baseline_trust[edge_id] = vector.trust
    state = InferenceLearningState(
        lineage_id=f"inference_{uuid.uuid4().hex[:12]}",
        baseline_training_run_id=frozen.training_run_id,
        baseline_relationship_digest=frozen.content_digest,
        trust_updates_enabled=updates_enabled,
        vectors=vectors,
        expected_success={edge_id: expected_success_initial for edge_id in vectors},
        baseline_trust=baseline_trust,
        trust_deltas_from_baseline={edge_id: 0.0 for edge_id in vectors},
        memories={name: [] for name in names},
        content_digest="pending",
    )
    return sign_state(state)


def propose_handoff_updates(
    shadow_state: InferenceLearningState,
    assessment: HandoffAssessment,
    *,
    producer_lead: str,
    producer_peer: str,
    alpha: float,
    step_max: float,
    expectation_beta: float,
) -> list[ShadowTrustUpdate]:
    """Apply deltas only to a shadow copy; the run-start prompt state never changes."""

    if not shadow_state.trust_updates_enabled:
        return []
    targets = (producer_lead, producer_peer)
    edges = {
        f"{consumer}->{producer}"
        for consumer in assessment.consumer_agents
        for producer in targets
    }
    # The downstream result also teaches the producing pair about their collaboration.
    edges.update(
        {
            f"{producer_lead}->{producer_peer}",
            f"{producer_peer}->{producer_lead}",
        }
    )
    updates: list[ShadowTrustUpdate] = []
    for edge_id in sorted(edges):
        edge = shadow_state.vectors[edge_id]
        expectation = shadow_state.expected_success[edge_id]
        rpe = float(assessment.reward) - expectation
        unclamped = alpha * rpe
        delta = max(-step_max, min(step_max, unclamped))
        before = edge.vector.trust
        after = max(-1.0, min(1.0, before + delta))
        actual_delta = after - before
        edge.vector.trust = after
        shadow_state.expected_success[edge_id] = max(
            0.0,
            min(1.0, expectation + expectation_beta * rpe),
        )
        shadow_state.trust_deltas_from_baseline[edge_id] = (
            after - shadow_state.baseline_trust[edge_id]
        )
        source, target = edge_id.split("->", 1)
        updates.append(
            ShadowTrustUpdate(
                update_id=f"shadow_{uuid.uuid4().hex[:12]}",
                run_id=assessment.run_id,
                producer_stage=assessment.producer_stage,
                consumer_stage=assessment.consumer_stage,
                source=source,
                target=target,
                reward=assessment.reward,
                expected_success_before=expectation,
                reward_prediction_error=rpe,
                alpha=alpha,
                unclamped_delta=unclamped,
                proposed_delta=actual_delta,
                trust_before=before,
                shadow_trust_after=after,
                evidence=assessment.evidence,
            )
        )
    sign_state(shadow_state)
    return updates


class InferenceLearningStore:
    """Optimistic, append-only commit store for cross-run inference learning."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.commits_dir = self.root / "commits"
        self.root.mkdir(parents=True, exist_ok=True)
        self.commits_dir.mkdir(parents=True, exist_ok=True)
        self.current_path = self.root / "inference_relationships.json"
        self.commit_log = self.root / "commits.jsonl"

    def load_or_initialize(
        self,
        frozen: FrozenRelationshipSnapshot,
        agent_names: Iterable[str],
        settings: dict,
    ) -> InferenceLearningState:
        lock_path = self.root / ".commit.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return self._load_or_initialize_locked(
                    frozen,
                    agent_names,
                    settings,
                )
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load_or_initialize_locked(
        self,
        frozen: FrozenRelationshipSnapshot,
        agent_names: Iterable[str],
        settings: dict,
    ) -> InferenceLearningState:
        if self.current_path.exists():
            state = InferenceLearningState.model_validate_json(self.current_path.read_text())
            if not verify_inference_state(state):
                raise ValueError("inference relationship state failed its digest check")
            if state.baseline_relationship_digest != frozen.content_digest:
                raise ValueError(
                    "inference state belongs to a different frozen training baseline"
                )
            return state
        state = initialize_inference_state(
            frozen,
            agent_names,
            updates_enabled=bool(settings["trust_updates_enabled"]),
            expected_success_initial=float(settings["expected_success_initial"]),
        )
        self._atomic_json(self.current_path, state.model_dump(mode="json"))
        self._atomic_json(self.root / "initial_relationships.json", state.model_dump(mode="json"))
        return state

    def commit(
        self,
        *,
        run_id: str,
        start_state: InferenceLearningState,
        shadow_state: InferenceLearningState,
        night: InferenceNightOutput,
    ) -> InferenceLearningState:
        lock_path = self.root / ".commit.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return self._commit_locked(
                    run_id=run_id,
                    start_state=start_state,
                    shadow_state=shadow_state,
                    night=night,
                )
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def invalidate_latest_commit(
        self,
        *,
        run_id: str,
        start_state: InferenceLearningState,
        invalid_end_digest: str,
        reason: str,
    ) -> dict:
        """Restore a run start after preserving a proven-invalid latest commit."""

        lock_path = self.root / ".commit.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                current = InferenceLearningState.model_validate_json(
                    self.current_path.read_text()
                )
                if not verify_inference_state(start_state) or not verify_inference_state(current):
                    raise ValueError("cannot invalidate an unverified inference state")
                if current.content_digest != invalid_end_digest:
                    raise RuntimeError("latest inference commit does not match invalid digest")
                if current.commit_sequence != start_state.commit_sequence + 1:
                    raise RuntimeError("only the latest single-run commit can be invalidated")
                version_path = self.commits_dir / (
                    f"{current.commit_sequence:06d}_{run_id}.json"
                )
                if not version_path.exists():
                    raise FileNotFoundError("versioned inference commit is missing")
                superseded = self.root / "superseded"
                superseded.mkdir(parents=True, exist_ok=True)
                suffix = invalid_end_digest.removeprefix("sha256:")[:12]
                preserved_state = superseded / f"{version_path.stem}_{suffix}.json"
                shutil.copy2(version_path, preserved_state)
                self._atomic_json(self.current_path, start_state.model_dump(mode="json"))
                record = {
                    "schema_version": "1.0",
                    "record_type": "commit_invalidation",
                    "run_id": run_id,
                    "invalidated_at": utc_now().isoformat(),
                    "invalid_end_digest": invalid_end_digest,
                    "restored_start_digest": start_state.content_digest,
                    "commit_sequence": current.commit_sequence,
                    "reason": reason,
                    "preserved_state": str(preserved_state.relative_to(self.root)),
                }
                with self.commit_log.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                return record
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _commit_locked(
        self,
        *,
        run_id: str,
        start_state: InferenceLearningState,
        shadow_state: InferenceLearningState,
        night: InferenceNightOutput,
    ) -> InferenceLearningState:
        current = InferenceLearningState.model_validate_json(self.current_path.read_text())
        if not verify_inference_state(current):
            raise ValueError("current inference state failed its digest check")
        if current.content_digest != start_state.content_digest:
            raise RuntimeError(
                "inference learning commit conflict: another run advanced the relationship state"
            )
        if not night.trust_commit_approved:
            raise RuntimeError("night workspace rejected the proposed trust commit")
        if shadow_state.baseline_relationship_digest != start_state.baseline_relationship_digest:
            raise ValueError("shadow state changed the immutable training baseline reference")
        if set(shadow_state.vectors) != set(start_state.vectors):
            raise ValueError("shadow state changed the directed edge set")
        for edge_id, start_edge in start_state.vectors.items():
            before = start_edge.vector.model_dump()
            after = shadow_state.vectors[edge_id].vector.model_dump()
            before.pop("trust")
            after.pop("trust")
            if before != after:
                raise ValueError(
                    f"inference attempted to change frozen non-trust dimensions on {edge_id}"
                )
        committed = shadow_state.model_copy(deep=True)
        committed.commit_sequence = current.commit_sequence + 1
        committed.updated_at = utc_now()
        for agent, lines in night.memory_updates.items():
            if agent not in committed.memories:
                raise ValueError(f"night workspace returned unknown agent memory: {agent}")
            committed.memories[agent] = list(lines)
        sign_state(committed)
        commit_payload = {
            "schema_version": "1.0",
            "run_id": run_id,
            "committed_at": committed.updated_at.isoformat(),
            "baseline_relationship_digest": committed.baseline_relationship_digest,
            "start_digest": start_state.content_digest,
            "end_digest": committed.content_digest,
            "commit_sequence": committed.commit_sequence,
            "trust_deltas_from_baseline": committed.trust_deltas_from_baseline,
            "night_workspace": night.model_dump(mode="json"),
        }
        version_path = self.commits_dir / (
            f"{committed.commit_sequence:06d}_{run_id}.json"
        )
        self._atomic_json(version_path, committed.model_dump(mode="json"))
        self._atomic_json(self.current_path, committed.model_dump(mode="json"))
        with self.commit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(commit_payload, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return committed

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
