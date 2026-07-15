from __future__ import annotations

import json

import pytest

from zerohandoff.training.corpus import (
    SolverTimeout,
    audit_corpus,
    generate_corpus,
    solve_grid,
)
from zerohandoff.training.puzzles import PuzzleRepository


def test_committed_real_corpus_is_reproducible_and_fully_audited(repo_root) -> None:
    committed = PuzzleRepository.load(repo_root / "data" / "puzzles.jsonl")
    regenerated = PuzzleRepository(generate_corpus(42))
    stats = json.loads((repo_root / "data" / "puzzle_stats.json").read_text())
    report = audit_corpus(committed.puzzles)

    assert committed.digest == regenerated.digest == stats["corpus_digest"]
    assert report["ok"] and stats["ok"]
    assert report["families"] == {"logic-grid": 8, "graph-route": 2}
    assert all(record["passed"] for record in report["records"])
    assert all(
        record.get("clue_minimal", True)
        and record.get("combined_solutions", 1) == 1
        and not record["answer_leakage"]
        for record in report["records"]
    )


def test_real_corpus_graders_accept_equivalent_formats_and_reject_wrong_answers(
    repo_root,
) -> None:
    puzzles = PuzzleRepository.load(repo_root / "data" / "puzzles.jsonl").puzzles
    for puzzle in puzzles:
        assert puzzle.grade(puzzle.answer)
        assert not puzzle.grade("incorrect")
    grid = puzzles[0]
    equivalent_grid = "\n".join(
        f"{position}) {row['pet']} with {row['person']}"
        for position, row in reversed(list(grid.spec["solution"].items()))
    )
    route = puzzles[-1]
    assert grid.grade(equivalent_grid)
    assert route.grade(" / ".join(route.spec["solution_route"]))
    assert not route.grade(
        " / ".join(route.spec["solution_route"]) + f" | cost {route.spec['solution_cost'] + 1}"
    )


def test_grid_solver_timeout_is_enforced(repo_root) -> None:
    puzzle = PuzzleRepository.load(repo_root / "data" / "puzzles.jsonl").puzzles[0]
    with pytest.raises(SolverTimeout):
        solve_grid(
            puzzle.spec,
            [*puzzle.clue_specs["A"], *puzzle.clue_specs["B"]],
            max_nodes=0,
        )


def test_real_training_corpus_is_distinct_from_fixture_corpus(repo_root) -> None:
    real = PuzzleRepository.load(repo_root / "data" / "puzzles.jsonl")
    fixture = PuzzleRepository.load(repo_root / "data" / "puzzles.fixture.json")
    assert real.digest != fixture.digest
    assert all(not puzzle.id.startswith("fixture-") for puzzle in real.puzzles)
    assert all(puzzle.spec and puzzle.clue_specs for puzzle in real.puzzles)
