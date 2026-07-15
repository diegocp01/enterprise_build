from __future__ import annotations

import json
from pathlib import Path

import pytest

from zerohandoff.models import InvocationStatus, Stage
from zerohandoff.runtime.fixture import FixtureAdapter
from zerohandoff.storage import RunStore
from zerohandoff.training.orchestrator import TrainingOrchestrator
from zerohandoff.training.puzzles import PuzzleRepository
from zerohandoff.trust import verify_frozen_snapshot


def test_full_fixture_training_has_exact_call_budget_and_frozen_vectors(
    tmp_path, repo_root, settings
) -> None:
    adapter = FixtureAdapter()
    puzzles = PuzzleRepository.load(repo_root / "data" / "puzzles.fixture.json")
    result = TrainingOrchestrator(
        settings=settings,
        adapter=adapter,
        puzzle_repository=puzzles,
        base_dir=tmp_path,
    ).run(run_id="train_ten_rounds", rounds=10)

    assert result.metrics["rounds_completed"] == 10
    assert result.metrics["episodes"] == 70
    assert result.metrics["agent_calls"] == 420
    assert len(adapter.calls) == 420
    assert result.frozen_snapshot is not None
    assert len(result.frozen_snapshot.vectors) == 14
    assert verify_frozen_snapshot(result.frozen_snapshot)

    store = RunStore(tmp_path, result.run_id, "training")
    assert len(store.read_jsonl(store.logs_dir / "episodes.jsonl")) == 70
    assert len(store.read_jsonl(store.logs_dir / "night_workspace.jsonl")) == 140
    assert len(store.read_jsonl(store.logs_dir / "memory_decisions.jsonl")) == 140
    policies = store.read_jsonl(store.logs_dir / "relationship_policies.jsonl")
    assert len(policies) == 280
    assert all(row["policy"]["source_vector_digest"] for row in policies)
    vector_rows = store.read_jsonl(store.logs_dir / "relationship_vectors.jsonl")
    assert len(vector_rows) == 11
    assert vector_rows[0]["round_id"] == 0
    assert all(
        value == 0.0
        for edge in vector_rows[0]["vectors"].values()
        for value in edge["vector"].values()
    )
    report = json.loads((result.store_root / "training_results.json").read_text())
    assert len(report["team_results"]) == 70
    assert len(report["agent_results"]) == 140
    assert report["call_summary"] == {
        "task_solving": 280,
        "curator": 140,
        "total_including_retries": 420,
    }
    assert all(row["timestamp"] for row in store.read_jsonl(store.logs_dir / "episodes.jsonl"))
    assert all("fixture_data" not in call.model_dump(mode="json") for call in adapter.calls)
    serialized = "\n".join(str(call.model_dump(mode="json")) for call in adapter.calls)
    assert "expected_answer" not in serialized
    assert all(call.permission_profile == "read-only" for call in adapter.calls)
    workspace_roots = {Path(call.workspace_root or "").name for call in adapter.calls}
    assert workspace_roots == {"day", "night"}
    assert not any((tmp_path / result.run_id / "workspace" / "day").iterdir())
    assert not any((tmp_path / result.run_id / "workspace" / "night").iterdir())
    curator = next(
        call for call in adapter.calls if call.inputs.get("purpose") == "night_curator"
    )
    assert len(curator.inputs["episode"]["messages"]) == 4
    assert set(curator.inputs["diary_evidence"]) == {
        agent.name for agent in settings.teams[Stage.SENSE].agents
    }
    assert curator.inputs["memory_settings"] == settings.memory
    assert curator.inputs["metaplasticity"] == settings.metaplasticity["default"]


def test_solver_backed_corpus_is_audited_before_training_calls(
    tmp_path, repo_root, settings
) -> None:
    adapter = FixtureAdapter()
    result = TrainingOrchestrator(
        settings=settings,
        adapter=adapter,
        puzzle_repository=PuzzleRepository.load(repo_root / "data" / "puzzles.jsonl"),
        base_dir=tmp_path,
    ).run(run_id="train_real_corpus_fixture", rounds=1)
    records = RunStore.read_jsonl(
        result.store_root / "logs" / "puzzle_validation.jsonl"
    )
    assert len(records) == 10
    assert all(record["passed"] for record in records)
    assert len(adapter.calls) == 42


def test_backend_error_skips_all_relationship_updates_for_affected_pair(
    tmp_path, repo_root, settings
) -> None:
    run_id = "train_backend_error"
    adapter = FixtureAdapter(
        {
            f"{run_id}-r1-SENSE-Mira-initial": {
                "status": InvocationStatus.BACKEND_ERROR.value,
            },
            f"{run_id}-r1-SENSE-Mira-initial-retry1": {
                "status": InvocationStatus.BACKEND_ERROR.value,
            },
        }
    )
    result = TrainingOrchestrator(
        settings=settings,
        adapter=adapter,
        puzzle_repository=PuzzleRepository.load(repo_root / "data" / "puzzles.fixture.json"),
        base_dir=tmp_path,
    ).run(run_id=run_id, rounds=1)
    assert result.frozen_snapshot is not None
    for edge_id in ("Mira->Zephyr", "Zephyr->Mira"):
        vector = result.frozen_snapshot.vectors[edge_id].vector
        assert all(value == 0.0 for value in vector.model_dump().values())
    episodes = RunStore(tmp_path, run_id, "training").read_jsonl(
        tmp_path / run_id / "logs" / "episodes.jsonl"
    )
    sense = next(row for row in episodes if row["team_id"] == Stage.SENSE.value)
    assert sense["status"] == "backend_error"
    assert sense["reward"] is None


def test_invalid_curator_output_commits_no_partial_round_state(
    tmp_path, repo_root, settings
) -> None:
    run_id = "train_invalid_curator"
    invalid = json.loads((repo_root / "tests/fixtures/invalid_curator_output.json").read_text())
    adapter = FixtureAdapter(
        {
            f"{run_id}-r1-Mira-curator": {"output": invalid},
            f"{run_id}-r1-Mira-curator-retry1": {"output": invalid},
        }
    )
    orchestrator = TrainingOrchestrator(
        settings=settings,
        adapter=adapter,
        puzzle_repository=PuzzleRepository.load(repo_root / "data" / "puzzles.fixture.json"),
        base_dir=tmp_path,
    )
    with pytest.raises(RuntimeError, match="night curator failed"):
        orchestrator.run(run_id=run_id, rounds=1)
    state = json.loads((tmp_path / run_id / "state.json").read_text())
    assert state["completed_rounds"] == []
    assert all(
        all(value == 0.0 for value in edge["vector"].values())
        for edge in state["relationships"].values()
    )
    assert not (tmp_path / run_id / "frozen_relationship_vectors.json").exists()


class CrashAfterFirstEpisode(FixtureAdapter):
    def invoke(self, invocation):
        if len(self.calls) == 4:
            raise RuntimeError("simulated process interruption")
        return super().invoke(invocation)


def test_training_resumes_after_an_episode_without_duplicate_calls_or_logs(
    tmp_path, repo_root, settings
) -> None:
    run_id = "train_resume_episode"
    puzzles = PuzzleRepository.load(repo_root / "data" / "puzzles.fixture.json")
    first = CrashAfterFirstEpisode()
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        TrainingOrchestrator(
            settings=settings,
            adapter=first,
            puzzle_repository=puzzles,
            base_dir=tmp_path,
        ).run(run_id=run_id, rounds=1)
    interrupted = json.loads((tmp_path / run_id / "state.json").read_text())
    assert list(interrupted["round_progress"]["1"]["episodes"]) == ["SENSE"]

    second = FixtureAdapter()
    result = TrainingOrchestrator(
        settings=settings,
        adapter=second,
        puzzle_repository=puzzles,
        base_dir=tmp_path,
    ).run(run_id=run_id, rounds=1)
    assert result.status.value == "completed"
    assert len(first.calls) + len(second.calls) == 42
    rows = RunStore.read_jsonl(tmp_path / run_id / "logs" / "episodes.jsonl")
    assert len(rows) == 7
    assert len({row["episode_id"] for row in rows}) == 7


class CrashDuringCurators(FixtureAdapter):
    def invoke(self, invocation):
        completed_curators = sum(
            call.inputs.get("purpose") == "night_curator" for call in self.calls
        )
        if invocation.inputs.get("purpose") == "night_curator" and completed_curators == 1:
            raise RuntimeError("simulated curator interruption")
        return super().invoke(invocation)


def test_training_resumes_mid_curator_without_duplicate_updates_or_calls(
    tmp_path, repo_root, settings
) -> None:
    run_id = "train_resume_curator"
    puzzles = PuzzleRepository.load(repo_root / "data" / "puzzles.fixture.json")
    first = CrashDuringCurators()
    with pytest.raises(RuntimeError, match="simulated curator interruption"):
        TrainingOrchestrator(
            settings=settings,
            adapter=first,
            puzzle_repository=puzzles,
            base_dir=tmp_path,
        ).run(run_id=run_id, rounds=1)
    interrupted = json.loads((tmp_path / run_id / "state.json").read_text())
    assert list(interrupted["round_progress"]["1"]["curators"]) == ["Mira"]

    second = FixtureAdapter()
    result = TrainingOrchestrator(
        settings=settings,
        adapter=second,
        puzzle_repository=puzzles,
        base_dir=tmp_path,
    ).run(run_id=run_id, rounds=1)
    assert result.status.value == "completed"
    assert len(first.calls) + len(second.calls) == 42
    store = RunStore(tmp_path, run_id, "training")
    assert len(store.read_jsonl(store.logs_dir / "agent_calls.jsonl")) == 42
    assert len(store.read_jsonl(store.logs_dir / "night_workspace.jsonl")) == 14
    assert len(store.read_jsonl(store.logs_dir / "night_updates.jsonl")) == 14
