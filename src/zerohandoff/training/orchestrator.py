from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from zerohandoff.config import SettingsBundle, digest_value
from zerohandoff.models import (
    AgentInvocation,
    EpisodeMessage,
    EpisodeRecord,
    EpisodeStatus,
    ExecutionMode,
    FrozenRelationshipSnapshot,
    InvocationStatus,
    NightCuratorOutput,
    RunManifest,
    RunStatus,
    Stage,
    utc_now,
)
from zerohandoff.runtime.base import RuntimeAdapter
from zerohandoff.storage import RunStore
from zerohandoff.training.puzzles import Puzzle, PuzzleRepository
from zerohandoff.trust import (
    apply_curator_deltas,
    compile_relationship_policy,
    freeze_relationships,
    initialize_relationships,
    qualitative_stance_for,
    select_lead,
    update_difficulty_expectation,
    update_trust_for_episode,
)


TRAINING_MESSAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "reasoning", "memory_ids_used", "diary_evidence"],
    "properties": {
        "answer": {"type": "string"},
        "reasoning": {"type": "string"},
        "memory_ids_used": {"type": "array", "items": {"type": "string"}},
        "diary_evidence": {"type": "string"},
    },
}


@dataclass(frozen=True)
class TrainingResult:
    run_id: str
    status: RunStatus
    store_root: Path
    frozen_snapshot: FrozenRelationshipSnapshot | None
    metrics: dict[str, Any]


def _git_commit(repo_root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


class TrainingOrchestrator:
    def __init__(
        self,
        *,
        settings: SettingsBundle,
        adapter: RuntimeAdapter,
        puzzle_repository: PuzzleRepository,
        base_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.puzzles = puzzle_repository
        self.base_dir = (base_dir or settings.repo_root / ".zerohandoff" / "training").resolve()
        self.git_commit = _git_commit(settings.repo_root)
        self._logged_invocations: dict[str, set[str]] = {}
        self._log_keys: dict[tuple[str, str], set[str]] = {}

    def initialize(self, run_id: str | None = None) -> tuple[RunStore, dict[str, Any]]:
        run_id = run_id or f"train_{uuid.uuid4().hex[:12]}"
        validation_report: dict[str, Any] | None = None
        if all(puzzle.spec and puzzle.clue_specs for puzzle in self.puzzles.puzzles):
            from zerohandoff.training.corpus import audit_corpus

            validation_report = audit_corpus(self.puzzles.puzzles)
            if not validation_report["ok"]:
                raise ValueError("puzzle corpus failed the pre-training validation gate")
        store = RunStore(self.base_dir, run_id, "training")
        if store.read_state() is not None:
            raise ValueError(f"training run already exists: {run_id}")
        day_workspace = store.workspace_dir / "day"
        night_workspace = store.workspace_dir / "night"
        day_workspace.mkdir(parents=True, exist_ok=True)
        night_workspace.mkdir(parents=True, exist_ok=True)
        relationships = initialize_relationships(self.settings.teams.values())
        difficulties = {puzzle.difficulty for puzzle in self.puzzles.puzzles}
        state = {
            "run_id": run_id,
            "status": RunStatus.CREATED.value,
            "completed_rounds": [],
            "round_progress": {},
            "relationships": {
                edge_id: relationship.model_dump(mode="json")
                for edge_id, relationship in relationships.items()
            },
            "difficulty_expectations": {
                difficulty: self.settings.training["difficulty_expectation_init"]
                for difficulty in sorted(difficulties)
            },
            "memories": {agent_name: [] for agent_name in self.settings.agents},
            "settings_digest": self.settings.digest,
            "puzzle_digest": self.puzzles.digest,
            "git_commit": self.git_commit,
        }
        store.atomic_json("settings.snapshot.json", self.settings.snapshot())
        store.atomic_json(
            "puzzles.snapshot.json",
            [p.model_dump(mode="json") for p in self.puzzles.puzzles],
        )
        store.write_state(state)
        manifest = RunManifest(
            run_id=run_id,
            run_type="training",
            status=RunStatus.CREATED,
            settings_digest=self.settings.digest,
            adapter=self.adapter.name,
            model_settings=self.settings.models,
        )
        store.write_manifest(manifest)
        store.append_event(
            event_type="training.initialized",
            status="completed",
            settings_digest=self.settings.digest,
            adapter=self.adapter.name,
            git_commit=self.git_commit,
            payload={"puzzle_digest": self.puzzles.digest},
        )
        store.append_log(
            "relationship_vectors",
            {
                "round_id": 0,
                "team_id": None,
                "episode_id": None,
                "puzzle_id": None,
                "puzzle_digest": self.puzzles.digest,
                "settings_digest": self.settings.digest,
                "model": None,
                "reasoning_effort": None,
                "git_commit": self.git_commit,
                "snapshot_type": "initialized",
                "vectors": state["relationships"],
            },
        )
        if validation_report is not None:
            for record in validation_report["records"]:
                store.append_log(
                    "puzzle_validation",
                    {
                        **record,
                        "puzzle_digest": next(
                            puzzle.digest
                            for puzzle in self.puzzles.puzzles
                            if puzzle.id == record["puzzle_id"]
                        ),
                        "settings_digest": self.settings.digest,
                        "git_commit": self.git_commit,
                    },
                )
        return store, state

    def run(self, *, run_id: str | None = None, rounds: int | None = None) -> TrainingResult:
        if run_id and (self.base_dir / run_id / "state.json").exists():
            store = RunStore(self.base_dir, run_id, "training")
            state = store.read_state()
            assert state is not None
            self._validate_resume_state(state)
        else:
            store, state = self.initialize(run_id)
        target_rounds = rounds or int(self.settings.training["rounds"])
        if target_rounds > len(self.puzzles.puzzles):
            raise ValueError("not enough puzzles for requested rounds")
        state["status"] = RunStatus.RUNNING.value
        store.write_state(state)
        store.append_event(
            event_type="training.started",
            status="running",
            settings_digest=self.settings.digest,
            adapter=self.adapter.name,
        )
        try:
            for round_id in range(1, target_rounds + 1):
                if round_id in state["completed_rounds"]:
                    continue
                self._run_round(store, state, round_id)
            snapshot = self._freeze(store, state)
            metrics = self._calculate_metrics(store, state)
            self._write_training_results(store, state, snapshot, metrics)
            state["status"] = RunStatus.COMPLETED.value
            state["frozen_snapshot"] = "frozen_relationship_vectors.json"
            store.write_state(state)
            manifest = RunManifest(
                run_id=store.run_id,
                run_type="training",
                status=RunStatus.COMPLETED,
                settings_digest=self.settings.digest,
                relationship_vector_digest=snapshot.content_digest,
                adapter=self.adapter.name,
                model_settings=self.settings.models,
                stage_outcomes={f"round_{i}": "completed" for i in state["completed_rounds"]},
                final_outcome="frozen",
            )
            manifest.completed_at = utc_now()
            store.write_manifest(manifest)
            store.append_event(
                event_type="training.completed",
                status="completed",
                settings_digest=self.settings.digest,
                relationship_vector_digest=snapshot.content_digest,
                output_refs=[
                    "frozen_relationship_vectors.json",
                    "metrics.json",
                    "training_results.json",
                ],
                payload={"rounds": len(state["completed_rounds"])},
            )
            return TrainingResult(store.run_id, RunStatus.COMPLETED, store.root, snapshot, metrics)
        except Exception as exc:
            state["status"] = RunStatus.FAILED.value
            state["failure_reason"] = str(exc)
            store.write_state(state)
            store.append_event(
                event_type="training.failed",
                status="failed",
                settings_digest=self.settings.digest,
                error={"code": type(exc).__name__, "message": str(exc)},
            )
            raise

    def _validate_resume_state(self, state: dict[str, Any]) -> None:
        if state["settings_digest"] != self.settings.digest:
            raise ValueError("settings digest changed; refusing to resume")
        if state["puzzle_digest"] != self.puzzles.digest:
            raise ValueError("puzzle digest changed; refusing to resume")

    def _run_round(self, store: RunStore, state: dict[str, Any], round_id: int) -> None:
        puzzle = self.puzzles.for_round(round_id)
        progress_key = str(round_id)
        progress = state.setdefault("round_progress", {}).get(progress_key)
        if progress is None:
            progress = {
                "puzzle_id": puzzle.id,
                "expectation_snapshot": dict(state["difficulty_expectations"]),
                "episodes": {},
            }
            state["round_progress"][progress_key] = progress
            store.write_state(state)
            store.append_event(
                event_type="training.round.started",
                status="running",
                stage=f"round_{round_id}",
                settings_digest=self.settings.digest,
                payload={
                    "puzzle_id": puzzle.id,
                    "expectations": progress["expectation_snapshot"],
                },
            )
        elif progress["puzzle_id"] != puzzle.id:
            raise ValueError("in-progress round puzzle changed; refusing to resume")
        expectation_snapshot = dict(progress["expectation_snapshot"])
        episodes_by_team = {
            team_id: EpisodeRecord.model_validate(payload)
            for team_id, payload in progress["episodes"].items()
        }
        for team in self.settings.teams.values():
            if team.stage.value in episodes_by_team:
                continue
            episode = self._run_episode(store, state, round_id, puzzle, team.stage.value)
            episodes_by_team[team.stage.value] = episode
            progress["episodes"][team.stage.value] = episode.model_dump(mode="json")
            store.write_state(state)
        episodes = [episodes_by_team[team.stage.value] for team in self.settings.teams.values()]
        if progress.get("post_trust_relationships"):
            relationships = self._relationships_from_payload(
                progress["post_trust_relationships"]
            )
        else:
            relationships = self._relationships_from_state(state)
            valid_rewards: dict[str, list[int]] = {}
            for episode in episodes:
                if episode.status == EpisodeStatus.BACKEND_ERROR or episode.reward is None:
                    continue
                team = self.settings.teams[Stage(episode.team_id)]
                changes = update_trust_for_episode(
                    relationships,
                    team,
                    reward=episode.reward,
                    expected_success=expectation_snapshot[episode.difficulty],
                    agreement=episode.agreement,
                    selected_lead=episode.selected_lead or team.agents[0].name,
                    alpha=float(self.settings.training["alpha"]),
                    step_max=float(self.settings.training["trust_step_max"]),
                )
                for change in changes:
                    self._append_log_once(
                        store,
                        "night_updates",
                        f"round:{round_id}:trust:{change['edge_id']}",
                        {
                            "round_id": round_id,
                            "team_id": episode.team_id,
                            "episode_id": episode.episode_id,
                            "puzzle_id": puzzle.id,
                            "puzzle_digest": puzzle.digest,
                            "settings_digest": self.settings.digest,
                            "model": None,
                            "reasoning_effort": None,
                            "git_commit": self.git_commit,
                            **change,
                        },
                    )
                valid_rewards.setdefault(episode.difficulty, []).append(episode.reward)
            difficulty_after = {
                difficulty: update_difficulty_expectation(
                    current,
                    valid_rewards.get(difficulty, []),
                    beta=float(self.settings.training["beta"]),
                )
                for difficulty, current in state["difficulty_expectations"].items()
            }
            progress["post_trust_relationships"] = {
                edge_id: relationship.model_dump(mode="json")
                for edge_id, relationship in relationships.items()
            }
            progress["difficulty_expectations_after"] = difficulty_after
            progress["curators"] = {}
            store.write_state(state)
        self._run_curators(store, state, round_id, episodes, relationships, progress)
        state["difficulty_expectations"] = progress["difficulty_expectations_after"]
        state["relationships"] = {
            edge_id: relationship.model_dump(mode="json")
            for edge_id, relationship in relationships.items()
        }
        state["completed_rounds"].append(round_id)
        state["round_progress"].pop(progress_key, None)
        store.write_state(state)
        self._append_log_once(
            store,
            "relationship_vectors",
            f"round:{round_id}:relationship_vectors",
            {
                "round_id": round_id,
                "team_id": None,
                "episode_id": None,
                "puzzle_id": puzzle.id,
                "puzzle_digest": puzzle.digest,
                "settings_digest": self.settings.digest,
                "model": None,
                "reasoning_effort": None,
                "git_commit": self.git_commit,
                "timestamp": None,
                "vectors": state["relationships"],
            },
        )
        store.append_event(
            event_type="training.round.completed",
            status="completed",
            stage=f"round_{round_id}",
            settings_digest=self.settings.digest,
            payload={"episodes": len(episodes)},
        )

    def _run_episode(
        self,
        store: RunStore,
        state: dict[str, Any],
        round_id: int,
        puzzle: Puzzle,
        team_id: str,
    ) -> EpisodeRecord:
        team = self.settings.teams[Stage(team_id)]
        left, right = team.agents
        relationships = self._relationships_from_state(state)
        messages: list[EpisodeMessage] = []
        raw_outputs: dict[tuple[str, str], dict[str, Any]] = {}
        started = time.monotonic()
        fixture_pattern = round_id % 3
        relationship_state_digest = digest_value(
            {
                edge_id: relationship.model_dump(mode="json")
                for edge_id, relationship in relationships.items()
            }
        )
        for turn in ("initial", "revised"):
            for position, (agent, peer) in enumerate(((left, right), (right, left))):
                if turn == "initial":
                    fixture_answer = puzzle.answer if position == 0 else "incorrect"
                elif fixture_pattern == 0:
                    fixture_answer = puzzle.answer
                elif fixture_pattern == 1:
                    fixture_answer = puzzle.answer if position == 0 else "incorrect"
                else:
                    fixture_answer = "incorrect"
                relationship_policy = compile_relationship_policy(
                    agent,
                    peer,
                    relationships,
                    self.settings.relationship_policy,
                )
                invocation = AgentInvocation(
                    invocation_id=f"{store.run_id}-r{round_id}-{team_id}-{agent.name}-{turn}",
                    run_id=store.run_id,
                    phase="training",
                    stage=f"round_{round_id}",
                    pair=team_id,
                    agent=agent,
                    mode=ExecutionMode.TRAINING,
                    objective=(
                        f"Solve {puzzle.id}; provide your {turn} answer and concise reasoning. "
                        "Use only the supplied puzzle inputs and do not inspect workspace files."
                    ),
                    inputs={
                        "purpose": f"training_{turn}",
                        "puzzle_id": puzzle.id,
                        "question": puzzle.question,
                        "shared_facts": [],
                        "private_clues": puzzle.private_clues["A" if position == 0 else "B"],
                        "peer_message": raw_outputs.get(
                            (peer.name, "initial" if turn == "revised" else turn), {}
                        ),
                        "own_previous_message": raw_outputs.get(
                            (agent.name, "initial"), {}
                        ) if turn == "revised" else {},
                        "memories": state["memories"][agent.name],
                    },
                    fixture_data={"answer": fixture_answer},
                    input_digests={
                        "puzzle": puzzle.digest,
                        "relationship_policy": digest_value(relationship_policy),
                    },
                    relationship_vector_digest=relationship_state_digest,
                    trust_stance=qualitative_stance_for(agent, peer, relationships),
                    relationship_policy=relationship_policy,
                    workspace_root=str(store.workspace_dir / "day"),
                    permission_profile="read-only",
                    output_schema=TRAINING_MESSAGE_SCHEMA,
                    model=self.settings.models["day"]["model"],
                    reasoning_effort=self.settings.models["day"]["reasoning_effort"],
                    timeout_seconds=int(self.settings.training["timeout_seconds"]),
                    retry_budget=int(self.settings.training["max_retries"]),
                )
                result = self._invoke_with_retries(store, invocation)
                if result.status != InvocationStatus.COMPLETED or result.output is None:
                    return self._failed_episode(
                        store,
                        round_id,
                        puzzle,
                        team_id,
                        left.name,
                        right.name,
                        result.status,
                        started,
                    )
                raw_outputs[(agent.name, turn)] = result.output
                messages.append(
                    EpisodeMessage(
                        agent=agent.name,
                        turn=turn,  # type: ignore[arg-type]
                        answer=str(result.output["answer"]),
                        reasoning=str(result.output["reasoning"]),
                    )
                )
        final_answers = {
            agent.name: str(raw_outputs[(agent.name, "revised")]["answer"])
            for agent in team.agents
        }
        agreement = self._normalize(final_answers[left.name]) == self._normalize(
            final_answers[right.name]
        )
        last_speaker = right.name
        if agreement:
            selected_lead = right.name
            reason = "agreement_last_speaker_records_submission"
            final_answer = final_answers[right.name]
        else:
            decision = select_lead(team, relationships, last_speaker=last_speaker)
            selected_lead = decision.lead.name
            reason = decision.reason
            final_answer = final_answers[selected_lead]
        reward = int(puzzle.grade(final_answer))
        episode = EpisodeRecord(
            run_id=store.run_id,
            round_id=round_id,
            team_id=team_id,
            episode_id=f"{store.run_id}-r{round_id}-{team_id}",
            puzzle_id=puzzle.id,
            puzzle_digest=puzzle.digest,
            difficulty=puzzle.difficulty,
            status=EpisodeStatus.COMPLETED,
            messages=messages,
            initial_answers={
                agent.name: str(raw_outputs[(agent.name, "initial")]["answer"])
                for agent in team.agents
            },
            final_answers=final_answers,
            final_answer=final_answer,
            selected_lead=selected_lead,
            resolution_reason=reason,
            reward=reward,
            agreement=agreement,
            productive_disagreement=not agreement and reward == 1,
            last_speaker=last_speaker,
            memory_ids_used={
                agent.name: list(raw_outputs[(agent.name, "revised")]["memory_ids_used"])
                for agent in team.agents
            },
            diary_evidence={
                agent.name: str(raw_outputs[(agent.name, "revised")]["diary_evidence"])
                for agent in team.agents
            },
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self._record_episode(store, episode)
        return episode

    def _failed_episode(
        self,
        store: RunStore,
        round_id: int,
        puzzle: Puzzle,
        team_id: str,
        left: str,
        right: str,
        invocation_status: InvocationStatus,
        started: float,
    ) -> EpisodeRecord:
        status_map = {
            InvocationStatus.TIMEOUT: EpisodeStatus.AGENT_TIMEOUT,
            InvocationStatus.INVALID_OUTPUT: EpisodeStatus.INVALID_AGENT_OUTPUT,
            InvocationStatus.BACKEND_ERROR: EpisodeStatus.BACKEND_ERROR,
            InvocationStatus.CANCELLED: EpisodeStatus.BACKEND_ERROR,
        }
        status = status_map.get(invocation_status, EpisodeStatus.INVALID_AGENT_OUTPUT)
        reward = None if status == EpisodeStatus.BACKEND_ERROR else 0
        episode = EpisodeRecord(
            run_id=store.run_id,
            round_id=round_id,
            team_id=team_id,
            episode_id=f"{store.run_id}-r{round_id}-{team_id}",
            puzzle_id=puzzle.id,
            puzzle_digest=puzzle.digest,
            difficulty=puzzle.difficulty,
            status=status,
            messages=[],
            initial_answers={},
            final_answers={},
            final_answer=None,
            selected_lead=None,
            resolution_reason=None,
            reward=reward,
            agreement=False,
            productive_disagreement=False,
            last_speaker=right,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self._record_episode(store, episode)
        return episode

    def _record_episode(self, store: RunStore, episode: EpisodeRecord) -> None:
        row = episode.model_dump(mode="json")
        row.update(
            {
                "settings_digest": self.settings.digest,
                "model": self.settings.models["day"]["model"],
                "reasoning_effort": self.settings.models["day"]["reasoning_effort"],
                "git_commit": self.git_commit,
                "timestamp": None,
            }
        )
        store.append_log("episodes", row)
        store.append_event(
            event_type="training.episode.completed",
            status=episode.status.value,
            stage=f"round_{episode.round_id}",
            actor=episode.team_id,
            adapter=self.adapter.name,
            model=self.settings.models["day"]["model"],
            reasoning_effort=self.settings.models["day"]["reasoning_effort"],
            settings_digest=self.settings.digest,
            duration_ms=episode.duration_ms,
            payload={
                "episode_id": episode.episode_id,
                "puzzle_id": episode.puzzle_id,
                "reward": episode.reward,
            },
        )

    def _run_curators(
        self,
        store: RunStore,
        state: dict[str, Any],
        round_id: int,
        episodes: list[EpisodeRecord],
        relationships: dict[str, Any],
        progress: dict[str, Any],
    ) -> None:
        episode_by_team = {episode.team_id: episode for episode in episodes}
        curator_progress = progress.setdefault("curators", {})
        for team in self.settings.teams.values():
            episode = episode_by_team[team.stage.value]
            for agent, peer in ((team.agents[0], team.agents[1]), (team.agents[1], team.agents[0])):
                edge_id = f"{agent.name}->{peer.name}"
                if agent.name in curator_progress:
                    from zerohandoff.models import DirectedRelationship

                    committed = curator_progress[agent.name]
                    relationships[edge_id] = DirectedRelationship.model_validate(
                        committed["relationship_after"]
                    )
                    state["memories"][agent.name] = list(committed["memory_after"])
                    continue
                expected_success = float(
                    progress["expectation_snapshot"][episode.difficulty]
                )
                prediction_error = (
                    None
                    if episode.reward is None
                    else float(episode.reward) - expected_success
                )
                memory_ids = sorted(
                    {
                        memory_id
                        for ids in episode.memory_ids_used.values()
                        for memory_id in ids
                    }
                )
                invocation = AgentInvocation(
                    invocation_id=f"{store.run_id}-r{round_id}-{agent.name}-curator",
                    run_id=store.run_id,
                    phase="training",
                    stage=f"round_{round_id}",
                    pair=team.stage.value,
                    agent=agent,
                    mode=ExecutionMode.CURATION,
                    objective=(
                        "Consolidate this agent's memory and update nine non-trust dimensions. "
                        "Use only the supplied evidence and do not inspect workspace files. "
                        "Return at most five final memory lines and at most 100 total words "
                        "across those lines."
                    ),
                    inputs={
                        "purpose": "night_curator",
                        "round_id": round_id,
                        "reward": episode.reward or 0,
                        "episode_ids": [episode.episode_id],
                        "episode": episode.model_dump(mode="json"),
                        "prediction_error": prediction_error,
                        "expected_success": expected_success,
                        "memory_ids": memory_ids,
                        "diary_evidence": episode.diary_evidence,
                        "current_memories": state["memories"][agent.name],
                        "memory_settings": self.settings.memory,
                        "metaplasticity": self.settings.metaplasticity.get(
                            agent.name,
                            self.settings.metaplasticity["default"],
                        ),
                        "peer_agent": peer.name,
                        "relationship_vector": relationships[
                            f"{agent.name}->{peer.name}"
                        ].model_dump(mode="json"),
                    },
                    workspace_root=str(store.workspace_dir / "night"),
                    permission_profile="read-only",
                    output_schema=NightCuratorOutput.model_json_schema(),
                    model=self.settings.models["night"]["model"],
                    reasoning_effort=self.settings.models["night"]["reasoning_effort"],
                    timeout_seconds=int(self.settings.training["timeout_seconds"]),
                    retry_budget=int(self.settings.training["max_retries"]),
                )
                curator = self._invoke_curator_with_retries(store, invocation)
                vector_update_skipped = episode.status == EpisodeStatus.BACKEND_ERROR
                if not vector_update_skipped:
                    relationships[edge_id] = apply_curator_deltas(
                        relationships[edge_id],
                        curator.relationship_deltas.model_dump(),
                    )
                before_memories = list(state["memories"][agent.name])
                state["memories"][agent.name] = curator.final_memory_lines
                self._append_log_once(
                    store,
                    "night_workspace",
                    f"round:{round_id}:curator:{agent.name}:workspace",
                    {
                        "round_id": round_id,
                        "team_id": team.stage.value,
                        "episode_id": episode.episode_id,
                        "puzzle_id": episode.puzzle_id,
                        "puzzle_digest": episode.puzzle_digest,
                        "settings_digest": self.settings.digest,
                        "model": invocation.model,
                        "reasoning_effort": invocation.reasoning_effort,
                        "git_commit": self.git_commit,
                        "timestamp": None,
                        "training_agent": agent.name,
                        "input_digest": digest_value(invocation.inputs),
                        "output": curator.model_dump(mode="json"),
                        "status": "completed",
                        "vector_update_skipped": vector_update_skipped,
                    },
                )
                self._append_log_once(
                    store,
                    "memory_decisions",
                    f"round:{round_id}:curator:{agent.name}:memory_decisions",
                    {
                        "round_id": round_id,
                        "team_id": team.stage.value,
                        "episode_id": episode.episode_id,
                        "puzzle_id": episode.puzzle_id,
                        "puzzle_digest": episode.puzzle_digest,
                        "settings_digest": self.settings.digest,
                        "model": invocation.model,
                        "reasoning_effort": invocation.reasoning_effort,
                        "git_commit": self.git_commit,
                        "training_agent": agent.name,
                        "retained_memory_ids": curator.retained_memory_ids,
                        "compressed_memories": curator.compressed_memories,
                        "forgotten_memory_ids": curator.forgotten_memory_ids,
                        "decision_reasons": curator.decision_reasons,
                        "evidence": curator.evidence,
                        "confidence": curator.confidence,
                    },
                )
                for forgotten_id in curator.forgotten_memory_ids:
                    self._append_log_once(
                        store,
                        "forgotten",
                        f"round:{round_id}:curator:{agent.name}:forgotten:{forgotten_id}",
                        {
                            "round_id": round_id,
                            "team_id": team.stage.value,
                            "episode_id": episode.episode_id,
                            "puzzle_id": episode.puzzle_id,
                            "puzzle_digest": episode.puzzle_digest,
                            "settings_digest": self.settings.digest,
                            "model": invocation.model,
                            "reasoning_effort": invocation.reasoning_effort,
                            "git_commit": self.git_commit,
                            "training_agent": agent.name,
                            "memory_id": forgotten_id,
                            "reason": curator.decision_reasons,
                            "salience_score": None,
                            "prediction_error": None,
                        },
                    )
                self._append_log_once(
                    store,
                    "memories_state",
                    f"round:{round_id}:curator:{agent.name}:memories_state",
                    {
                        "round_id": round_id,
                        "team_id": team.stage.value,
                        "episode_id": episode.episode_id,
                        "puzzle_id": episode.puzzle_id,
                        "puzzle_digest": episode.puzzle_digest,
                        "settings_digest": self.settings.digest,
                        "model": invocation.model,
                        "reasoning_effort": invocation.reasoning_effort,
                        "git_commit": self.git_commit,
                        "timestamp": None,
                        "training_agent": agent.name,
                        "before": before_memories,
                        "after": curator.final_memory_lines,
                    },
                )
                curator_progress[agent.name] = {
                    "relationship_after": relationships[edge_id].model_dump(mode="json"),
                    "memory_after": list(curator.final_memory_lines),
                    "output_digest": digest_value(curator),
                }
                store.write_state(state)

    def _log_agent_result(self, store: RunStore, invocation: AgentInvocation, result: Any) -> None:
        root_key = str(store.root)
        logged = self._logged_invocations.get(root_key)
        if logged is None:
            logged = {
                row.get("invocation", {}).get("invocation_id")
                for row in store.read_jsonl(store.logs_dir / "agent_calls.jsonl")
                if row.get("invocation", {}).get("invocation_id")
                and row.get("result", {}).get("status") == InvocationStatus.COMPLETED.value
            }
            self._logged_invocations[root_key] = logged
        if invocation.invocation_id in logged:
            return
        store.append_log(
            "agent_calls",
            {
                "invocation": invocation.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            },
        )
        if invocation.relationship_policy is not None:
            store.append_log(
                "relationship_policies",
                {
                    "invocation_id": invocation.invocation_id,
                    "stage": invocation.stage,
                    "team_id": invocation.pair,
                    "actor": invocation.agent.name,
                    "purpose": invocation.inputs.get("purpose"),
                    "settings_digest": self.settings.digest,
                    "relationship_vector_digest": invocation.relationship_vector_digest,
                    "policy": invocation.relationship_policy.model_dump(mode="json"),
                },
            )
        store.append_event(
            event_type="agent.invocation.completed",
            status=result.status.value,
            stage=invocation.stage,
            actor=invocation.agent.name,
            adapter=self.adapter.name,
            model=invocation.model,
            reasoning_effort=invocation.reasoning_effort,
            settings_digest=self.settings.digest,
            attempt=invocation.attempt,
            duration_ms=result.duration_ms,
            usage=result.usage,
            git_commit=self.git_commit,
            error=result.error,
            payload={"invocation_id": invocation.invocation_id, "pair": invocation.pair},
        )
        if result.status == InvocationStatus.COMPLETED:
            logged.add(invocation.invocation_id)

    def _append_log_once(
        self,
        store: RunStore,
        category: str,
        idempotency_key: str,
        value: dict[str, Any],
    ) -> None:
        cache_key = (str(store.root), category)
        keys = self._log_keys.get(cache_key)
        if keys is None:
            keys = {
                row["idempotency_key"]
                for row in store.read_jsonl(store.logs_dir / f"{category}.jsonl")
                if row.get("idempotency_key")
            }
            self._log_keys[cache_key] = keys
        if idempotency_key in keys:
            return
        store.append_log(category, {**value, "idempotency_key": idempotency_key})
        keys.add(idempotency_key)

    def _invoke_with_retries(self, store: RunStore, invocation: AgentInvocation) -> Any:
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
            self._log_agent_result(store, current, result)
            if result.status == InvocationStatus.COMPLETED:
                return result
            if result.status == InvocationStatus.CANCELLED:
                return result
        assert result is not None
        return result

    def _invoke_curator_with_retries(
        self,
        store: RunStore,
        invocation: AgentInvocation,
    ) -> NightCuratorOutput:
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
            curator = None
            if result.status == InvocationStatus.COMPLETED and result.output is not None:
                try:
                    curator = NightCuratorOutput.model_validate(result.output)
                except ValidationError as exc:
                    result = result.model_copy(
                        update={
                            "status": InvocationStatus.INVALID_OUTPUT,
                            "error": {
                                "code": "invalid_curator_contract",
                                "message": str(exc),
                            },
                        }
                    )
            self._log_agent_result(store, current, result)
            if curator is not None:
                return curator
            if result.status == InvocationStatus.CANCELLED:
                break
        assert result is not None
        raise RuntimeError(
            f"night curator failed for {invocation.agent.name}: {result.status.value}"
        )

    def _freeze(self, store: RunStore, state: dict[str, Any]) -> FrozenRelationshipSnapshot:
        relationships = self._relationships_from_state(state)
        snapshot = freeze_relationships(store.run_id, relationships)
        store.atomic_json("frozen_relationship_vectors.json", snapshot)
        return snapshot

    def _calculate_metrics(self, store: RunStore, state: dict[str, Any]) -> dict[str, Any]:
        episodes = store.read_jsonl(store.logs_dir / "episodes.jsonl")
        valid = [episode for episode in episodes if episode["reward"] is not None]
        metrics = {
            "rounds_completed": len(state["completed_rounds"]),
            "episodes": len(episodes),
            "valid_episodes": len(valid),
            "wins": sum(int(episode["reward"] or 0) for episode in valid),
            "productive_disagreements": sum(
                bool(episode["productive_disagreement"]) for episode in valid
            ),
            "agent_calls": len(store.read_jsonl(store.logs_dir / "agent_calls.jsonl")),
            "difficulty_expectations": state["difficulty_expectations"],
        }
        store.atomic_json("metrics.json", metrics)
        return metrics

    def _write_training_results(
        self,
        store: RunStore,
        state: dict[str, Any],
        snapshot: FrozenRelationshipSnapshot,
        metrics: dict[str, Any],
    ) -> Path:
        episodes = store.read_jsonl(store.logs_dir / "episodes.jsonl")
        calls = store.read_jsonl(store.logs_dir / "agent_calls.jsonl")
        curator_commits = store.read_jsonl(store.logs_dir / "night_workspace.jsonl")
        team_results = [
            {
                key: episode.get(key)
                for key in (
                    "round_id",
                    "puzzle_id",
                    "difficulty",
                    "team_id",
                    "status",
                    "reward",
                    "agreement",
                    "productive_disagreement",
                    "selected_lead",
                    "resolution_reason",
                    "final_answer",
                    "duration_ms",
                    "timestamp",
                )
            }
            for episode in episodes
        ]
        puzzles_by_id = {puzzle.id: puzzle for puzzle in self.puzzles.puzzles}
        agent_results = []
        for episode in episodes:
            team = self.settings.teams[Stage(episode["team_id"])]
            puzzle = puzzles_by_id[episode["puzzle_id"]]
            for agent in team.agents:
                initial_answer = episode.get("initial_answers", {}).get(agent.name)
                revised_answer = episode.get("final_answers", {}).get(agent.name)
                agent_results.append(
                    {
                        "round_id": episode["round_id"],
                        "puzzle_id": episode["puzzle_id"],
                        "difficulty": episode["difficulty"],
                        "team_id": episode["team_id"],
                        "agent": agent.name,
                        "status": episode["status"],
                        "initial_answer": initial_answer,
                        "initial_correct": bool(
                            initial_answer and puzzle.grade(initial_answer)
                        ),
                        "revised_answer": revised_answer,
                        "revised_correct": bool(
                            revised_answer and puzzle.grade(revised_answer)
                        ),
                        "selected_lead": episode.get("selected_lead") == agent.name,
                        "team_reward": episode.get("reward"),
                        "memory_ids_used": episode.get("memory_ids_used", {}).get(
                            agent.name,
                            [],
                        ),
                        "diary_evidence": episode.get("diary_evidence", {}).get(agent.name),
                        "timestamp": episode.get("timestamp"),
                    }
                )
        report = {
            "schema_version": "1.0",
            "run_id": store.run_id,
            "generated_at": utc_now().isoformat(),
            "settings_digest": self.settings.digest,
            "puzzle_digest": self.puzzles.digest,
            "git_commit": self.git_commit,
            "model_settings": self.settings.models,
            "metrics": metrics,
            "call_summary": {
                # An episode is committed only after both agents complete their
                # initial and revised turns, so it proves four accepted solver
                # calls. A night-workspace row is the durable Curator commit.
                # Raw agent-call rows may include rejected or retried attempts.
                "task_solving": len(episodes) * 4,
                "curator": len(curator_commits),
                "total_including_retries": len(calls),
            },
            "team_results": team_results,
            "agent_results": agent_results,
            "final_relationship_vectors": {
                edge_id: relationship.model_dump(mode="json")
                for edge_id, relationship in sorted(snapshot.vectors.items())
            },
            "frozen_relationship_vector_digest": snapshot.content_digest,
            "frozen_at": snapshot.frozen_at.isoformat(),
            "rounds_completed": state["completed_rounds"],
        }
        return store.atomic_json("training_results.json", report)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.lower().strip().split())

    @staticmethod
    def _relationships_from_state(state: dict[str, Any]) -> dict[str, Any]:
        return TrainingOrchestrator._relationships_from_payload(state["relationships"])

    @staticmethod
    def _relationships_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
        from zerohandoff.models import DirectedRelationship

        return {
            edge_id: DirectedRelationship.model_validate(relationship_payload)
            for edge_id, relationship_payload in payload.items()
        }
