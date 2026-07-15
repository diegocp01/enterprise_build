from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from zerohandoff.config import digest_value
from zerohandoff.models import Contract


class Puzzle(Contract):
    id: str
    family: str
    difficulty: str
    question: str
    private_clues: dict[str, list[str]]
    answer: str
    acceptable_answers: list[str] = Field(default_factory=list)
    answer_type: str = "text"
    spec: dict[str, Any] = Field(default_factory=dict)
    clue_specs: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    solver_telemetry: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def private_clue_split_is_complete(self) -> Puzzle:
        if set(self.private_clues) != {"A", "B"}:
            raise ValueError("private clues must contain exactly A and B")
        if not self.private_clues["A"] or not self.private_clues["B"]:
            raise ValueError("both agents need at least one private clue")
        return self

    @property
    def digest(self) -> str:
        return digest_value(self.model_dump(mode="json"))

    def grade(self, candidate: str) -> bool:
        if self.answer_type == "grid_assignment":
            return self._grade_grid(candidate)
        if self.answer_type == "graph_route":
            return self._grade_route(candidate)
        normalized = " ".join(candidate.lower().strip().split())
        accepted = [self.answer, *self.acceptable_answers]
        return normalized in {" ".join(answer.lower().strip().split()) for answer in accepted}

    def _grade_grid(self, candidate: str) -> bool:
        expected = self.spec.get("solution", {})
        people = [row["person"] for row in expected.values()]
        pets = [row["pet"] for row in expected.values()]
        parsed: dict[str, dict[str, str]] = {}
        for segment in re.split(r"[;\n]+", candidate.lower()):
            position_match = re.search(r"(?:house\s*)?(\d+)", segment)
            if not position_match:
                continue
            person_hits = [
                person
                for person in people
                if re.search(rf"\b{re.escape(person.lower())}\b", segment)
            ]
            pet_hits = [
                pet for pet in pets if re.search(rf"\b{re.escape(pet.lower())}\b", segment)
            ]
            if len(person_hits) == 1 and len(pet_hits) == 1:
                parsed[position_match.group(1)] = {
                    "person": person_hits[0],
                    "pet": pet_hits[0],
                }
        return parsed == expected

    def _grade_route(self, candidate: str) -> bool:
        expected_route = self.spec.get("solution_route", [])
        nodes = sorted(self.spec.get("nodes", []), key=len, reverse=True)
        if not expected_route or not nodes:
            return False
        node_pattern = re.compile(
            r"\b(" + "|".join(re.escape(node) for node in nodes) + r")\b",
            re.IGNORECASE,
        )
        route = [match.group(1).lower() for match in node_pattern.finditer(candidate)]
        if route != [node.lower() for node in expected_route]:
            return False
        cost_match = re.search(r"\bcost\s*[:=]?\s*(\d+)\b", candidate, re.IGNORECASE)
        return not cost_match or int(cost_match.group(1)) == int(self.spec["solution_cost"])


class PuzzleRepository:
    def __init__(self, puzzles: list[Puzzle]) -> None:
        if len({puzzle.id for puzzle in puzzles}) != len(puzzles):
            raise ValueError("puzzle IDs must be unique")
        self.puzzles = puzzles

    @classmethod
    def load(cls, path: Path) -> PuzzleRepository:
        if path.suffix == ".jsonl":
            values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        else:
            values = json.loads(path.read_text())
        return cls([Puzzle.model_validate(item) for item in values])

    @property
    def digest(self) -> str:
        return digest_value([puzzle.model_dump(mode="json") for puzzle in self.puzzles])

    def for_round(self, round_id: int) -> Puzzle:
        if not 1 <= round_id <= len(self.puzzles):
            raise IndexError("round has no puzzle")
        return self.puzzles[round_id - 1]
