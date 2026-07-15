from __future__ import annotations

import itertools
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from zerohandoff.config import digest_value
from zerohandoff.training.puzzles import Puzzle, PuzzleRepository


DIFFICULTY_RANK = {"easy": 1, "medium": 2, "hard": 3, "expert": 4}

PEOPLE = (
    ("Ava", "Bruno", "Cora"),
    ("Dara", "Eli", "Faye"),
    ("Gina", "Hugo", "Iris", "Jonah"),
    ("Kira", "Luis", "Maya", "Noel"),
    ("Omar", "Priya", "Quinn", "Rosa"),
    ("Sana", "Theo", "Uma", "Viktor", "Wren"),
    ("Xena", "Yuri", "Zara", "Alden", "Bree"),
    ("Caleb", "Dina", "Esme", "Farah", "Gavin"),
)

PETS = (
    ("cat", "dog", "owl"),
    ("fox", "hare", "ibis"),
    ("koala", "lynx", "mole", "newt"),
    ("otter", "panda", "quail", "raven"),
    ("seal", "tiger", "urchin", "vole"),
    ("wolf", "yak", "zebu", "badger", "crane"),
    ("dingo", "egret", "ferret", "gecko", "heron"),
    ("iguana", "jackal", "lemur", "marten", "narwhal"),
)

GRID_CONFIGS = (
    (3, "easy"),
    (3, "easy"),
    (4, "medium"),
    (4, "medium"),
    (4, "medium"),
    (5, "hard"),
    (5, "hard"),
    (5, "hard"),
)


class SolverTimeout(RuntimeError):
    pass


def _position_map(permutation: tuple[str, ...]) -> dict[str, int]:
    return {item: index for index, item in enumerate(permutation, 1)}


def _clue_holds(clue: dict[str, Any], positions: dict[str, dict[str, int]]) -> bool:
    kind = clue["kind"]
    left = positions[clue["category"]][clue["item"]]
    if kind == "fixed":
        return left == clue["position"]
    if kind == "not_position":
        return left != clue["position"]
    if kind == "either_position":
        return left in clue["positions"]
    right = positions[clue["other_category"]][clue["other_item"]]
    if kind == "same":
        return left == right
    if kind == "before":
        return left < right
    if kind == "adjacent":
        return abs(left - right) == 1
    if kind == "distance":
        return abs(left - right) == clue["distance"]
    raise ValueError(f"unknown clue kind: {kind}")


def solve_grid(
    spec: dict[str, Any],
    clues: list[dict[str, Any]],
    *,
    limit: int | None = None,
    max_nodes: int | None = None,
) -> tuple[list[dict[str, list[str]]], int]:
    people = tuple(spec["people"])
    pets = tuple(spec["pets"])
    solutions: list[dict[str, list[str]]] = []
    nodes = 0
    for people_order in itertools.permutations(people):
        people_positions = _position_map(people_order)
        people_only = [
            clue
            for clue in clues
            if clue["category"] == "people"
            and clue.get("other_category", "people") == "people"
        ]
        if not all(_clue_holds(clue, {"people": people_positions}) for clue in people_only):
            continue
        for pets_order in itertools.permutations(pets):
            nodes += 1
            if max_nodes is not None and nodes > max_nodes:
                raise SolverTimeout("grid solver operation budget exhausted")
            positions = {
                "people": people_positions,
                "pets": _position_map(pets_order),
            }
            if all(_clue_holds(clue, positions) for clue in clues):
                solutions.append({"people": list(people_order), "pets": list(pets_order)})
                if limit is not None and len(solutions) >= limit:
                    return solutions, nodes
    return solutions, nodes


def _clue_text(clue: dict[str, Any]) -> str:
    item = clue["item"]
    kind = clue["kind"]
    if kind == "fixed":
        return f"{item} is in house {clue['position']}."
    if kind == "not_position":
        return f"{item} is not in house {clue['position']}."
    if kind == "either_position":
        first, second = clue["positions"]
        return f"{item} is in house {first} or house {second}."
    other = clue["other_item"]
    if kind == "same":
        return f"{item} lives in the same house as the {other}."
    if kind == "before":
        return f"{item} is in a lower-numbered house than the {other}."
    if kind == "adjacent":
        return f"{item} lives next to the {other}."
    if kind == "distance":
        return f"{item} and the {other} are {clue['distance']} houses apart."
    raise ValueError(f"unknown clue kind: {kind}")


def _deduplicate_clues(clues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for clue in clues:
        unique[json.dumps(clue, sort_keys=True, separators=(",", ":"))] = clue
    return list(unique.values())


def _true_clue_pool(
    people_order: tuple[str, ...],
    pets_order: tuple[str, ...],
    difficulty: str,
    rng: random.Random,
) -> list[dict[str, Any]]:
    positions = {
        "people": _position_map(people_order),
        "pets": _position_map(pets_order),
    }
    size = len(people_order)
    clues: list[dict[str, Any]] = []
    for category, items in (("people", people_order), ("pets", pets_order)):
        for item in items:
            position = positions[category][item]
            clues.append(
                {"kind": "fixed", "category": category, "item": item, "position": position}
            )
            wrong_positions = [
                candidate for candidate in range(1, size + 1) if candidate != position
            ]
            rng.shuffle(wrong_positions)
            for wrong in wrong_positions[:2]:
                clues.append(
                    {
                        "kind": "not_position",
                        "category": category,
                        "item": item,
                        "position": wrong,
                    }
                )
            alternative = wrong_positions[0]
            clues.append(
                {
                    "kind": "either_position",
                    "category": category,
                    "item": item,
                    "positions": sorted([position, alternative]),
                }
            )

    categories = (("people", people_order), ("pets", pets_order))
    references = [(category, item) for category, items in categories for item in items]
    for index, (category, item) in enumerate(references):
        left = positions[category][item]
        for other_category, other_item in references[index + 1 :]:
            right = positions[other_category][other_item]
            if left == right and category != other_category:
                clues.append(
                    {
                        "kind": "same",
                        "category": category,
                        "item": item,
                        "other_category": other_category,
                        "other_item": other_item,
                    }
                )
            if left != right:
                first = (category, item, left)
                second = (other_category, other_item, right)
                if left > right:
                    first, second = second, first
                clues.append(
                    {
                        "kind": "before",
                        "category": first[0],
                        "item": first[1],
                        "other_category": second[0],
                        "other_item": second[1],
                    }
                )
            if abs(left - right) == 1:
                clues.append(
                    {
                        "kind": "adjacent",
                        "category": category,
                        "item": item,
                        "other_category": other_category,
                        "other_item": other_item,
                    }
                )
            if abs(left - right) >= 2:
                clues.append(
                    {
                        "kind": "distance",
                        "category": category,
                        "item": item,
                        "other_category": other_category,
                        "other_item": other_item,
                        "distance": abs(left - right),
                    }
                )

    allowed = {
        "easy": {"fixed", "same", "adjacent"},
        "medium": {"fixed", "same", "before", "adjacent", "not_position", "either_position"},
        "hard": {
            "fixed",
            "same",
            "before",
            "adjacent",
            "distance",
            "not_position",
            "either_position",
        },
    }[difficulty]
    return _deduplicate_clues([clue for clue in clues if clue["kind"] in allowed])


def _minimal_unique_clues(
    spec: dict[str, Any],
    pool: list[dict[str, Any]],
    difficulty: str,
    rng: random.Random,
) -> list[dict[str, Any]]:
    active = list(pool)
    tie_breaks = {id(clue): rng.random() for clue in active}
    if difficulty == "easy":
        priority = {"same": 0, "adjacent": 0, "fixed": 2}
    elif difficulty == "medium":
        priority = {"fixed": 0, "same": 1, "adjacent": 1, "before": 2}
    else:
        priority = {"fixed": 0, "not_position": 0, "either_position": 1}
    removal_order = sorted(
        active,
        key=lambda clue: (priority.get(clue["kind"], 2), tie_breaks[id(clue)]),
    )
    for clue in removal_order:
        trial = [candidate for candidate in active if candidate is not clue]
        solutions, _ = solve_grid(spec, trial, limit=2)
        if len(solutions) == 1:
            active = trial
    solutions, _ = solve_grid(spec, active, limit=2)
    if len(solutions) != 1:
        raise ValueError("generated grid does not have exactly one solution")
    if len(active) < 2:
        raise ValueError("generated grid does not have enough clues to split")
    return active


def _canonical_grid_answer(solution: dict[str, list[str]]) -> str:
    return "; ".join(
        f"house {index}: {person} / {pet}"
        for index, (person, pet) in enumerate(
            zip(solution["people"], solution["pets"], strict=True), 1
        )
    )


def _grid_solution_payload(solution: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    return {
        str(index): {"person": person, "pet": pet}
        for index, (person, pet) in enumerate(
            zip(solution["people"], solution["pets"], strict=True), 1
        )
    }


def _make_grid(index: int, size: int, difficulty: str, seed: int) -> Puzzle:
    rng = random.Random(seed + index * 997)
    people = list(PEOPLE[index - 1][:size])
    pets = list(PETS[index - 1][:size])
    people_order = tuple(rng.sample(people, len(people)))
    pets_order = tuple(rng.sample(pets, len(pets)))
    spec = {
        "size": size,
        "people": sorted(people),
        "pets": sorted(pets),
    }
    pool = _true_clue_pool(people_order, pets_order, difficulty, rng)
    clues = _minimal_unique_clues(spec, pool, difficulty, rng)
    rng.shuffle(clues)
    split = {"A": clues[::2], "B": clues[1::2]}
    if not split["A"] or not split["B"]:
        raise ValueError("generated clue split is empty")
    full_solution, nodes = solve_grid(spec, clues, limit=2)
    if len(full_solution) != 1:
        raise ValueError("generated grid lost uniqueness")
    solution = full_solution[0]
    solution_payload = _grid_solution_payload(solution)
    question = (
        f"Arrange {', '.join(sorted(people))} and the pets {', '.join(sorted(pets))} "
        f"across houses 1 through {size}, one person and one pet per house. Your teammate "
        "has different private clues; explain the clues you used, combine both halves, and "
        "return every house as 'house N: person / pet'."
    )
    return Puzzle(
        id=f"grid-{index:02d}",
        family="logic-grid",
        difficulty=difficulty,
        question=question,
        private_clues={
            side: [_clue_text(clue) for clue in values]
            for side, values in split.items()
        },
        answer=_canonical_grid_answer(solution),
        answer_type="grid_assignment",
        spec={**spec, "solution": solution_payload},
        clue_specs=split,
        solver_telemetry={
            "search_space": math.factorial(size) ** 2,
            "full_solver_nodes": nodes,
            "clue_count": len(clues),
            "indirect_clues": sum(clue["kind"] != "fixed" for clue in clues),
        },
    )


def _all_simple_routes(
    nodes: list[str],
    edges: list[dict[str, Any]],
    start: str,
    end: str,
    *,
    max_nodes: int | None = None,
) -> tuple[list[tuple[int, tuple[str, ...]]], int]:
    adjacency: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for edge in edges:
        adjacency[edge["from"]].append((edge["to"], int(edge["weight"])))
        adjacency[edge["to"]].append((edge["from"], int(edge["weight"])))
    routes: list[tuple[int, tuple[str, ...]]] = []
    visited_nodes = 0

    def walk(node: str, path: tuple[str, ...], cost: int) -> None:
        nonlocal visited_nodes
        visited_nodes += 1
        if max_nodes is not None and visited_nodes > max_nodes:
            raise SolverTimeout("route solver operation budget exhausted")
        if node == end:
            routes.append((cost, path))
            return
        for neighbor, weight in adjacency[node]:
            if neighbor not in path:
                walk(neighbor, (*path, neighbor), cost + weight)

    walk(start, (start,), 0)
    return sorted(routes), visited_nodes


def _route_clue(edge: dict[str, Any]) -> str:
    return f"The undirected edge {edge['from']}—{edge['to']} has weight {edge['weight']}."


def _make_route(
    index: int,
    difficulty: str,
    nodes: list[str],
    edges_a: list[tuple[str, str, int]],
    edges_b: list[tuple[str, str, int]],
    start: str,
    end: str,
) -> Puzzle:
    def as_edges(values: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
        return [
            {"from": left, "to": right, "weight": weight}
            for left, right, weight in values
        ]

    split = {"A": as_edges(edges_a), "B": as_edges(edges_b)}
    edges = [*split["A"], *split["B"]]
    routes, nodes_visited = _all_simple_routes(nodes, edges, start, end)
    if not routes:
        raise ValueError("route puzzle has no path")
    minimum = routes[0][0]
    shortest = [route for route in routes if route[0] == minimum]
    if len(shortest) != 1:
        raise ValueError("route puzzle shortest path is not unique")
    route = list(shortest[0][1])
    question = (
        f"Find the unique lowest-cost route from {start} to {end} in an undirected weighted "
        "graph whose nodes are "
        f"{', '.join(nodes)}. Your teammate has different private edges; explain your edges, "
        "combine both halves, and return the ordered route plus total cost."
    )
    return Puzzle(
        id=f"route-{index:02d}",
        family="graph-route",
        difficulty=difficulty,
        question=question,
        private_clues={
            side: [_route_clue(edge) for edge in values]
            for side, values in split.items()
        },
        answer=f"{' -> '.join(route)} | cost {minimum}",
        answer_type="graph_route",
        spec={
            "nodes": nodes,
            "edges": edges,
            "start": start,
            "end": end,
            "solution_route": route,
            "solution_cost": minimum,
        },
        clue_specs=split,
        solver_telemetry={
            "search_space": 2 ** len(edges),
            "full_solver_nodes": nodes_visited,
            "edge_count": len(edges),
        },
    )


def generate_corpus(seed: int = 42) -> list[Puzzle]:
    puzzles = [
        _make_grid(index, size, difficulty, seed)
        for index, (size, difficulty) in enumerate(GRID_CONFIGS, 1)
    ]
    puzzles.append(
        _make_route(
            9,
            "hard",
            ["Cedar", "Maple", "Oak", "Pine", "Birch", "Elm"],
            [
                ("Cedar", "Maple", 3),
                ("Oak", "Elm", 3),
                ("Pine", "Birch", 4),
                ("Maple", "Pine", 4),
            ],
            [
                ("Maple", "Oak", 2),
                ("Cedar", "Pine", 5),
                ("Birch", "Elm", 5),
                ("Oak", "Birch", 6),
            ],
            "Cedar",
            "Elm",
        )
    )
    puzzles.append(
        _make_route(
            10,
            "expert",
            ["Amber", "Bronze", "Cobalt", "Denim", "Emerald", "Frost", "Gold"],
            [
                ("Amber", "Cobalt", 4),
                ("Cobalt", "Emerald", 3),
                ("Frost", "Gold", 2),
                ("Amber", "Bronze", 6),
                ("Denim", "Frost", 7),
            ],
            [
                ("Emerald", "Frost", 2),
                ("Bronze", "Denim", 4),
                ("Denim", "Gold", 8),
                ("Cobalt", "Denim", 7),
                ("Bronze", "Emerald", 7),
            ],
            "Amber",
            "Gold",
        )
    )
    return puzzles


def _normalized_leak(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _equivalent_answer(puzzle: Puzzle) -> str:
    if puzzle.answer_type == "grid_assignment":
        solution = puzzle.spec["solution"]
        return "\n".join(
            f"{position}) {row['pet']} with {row['person']}"
            for position, row in reversed(list(solution.items()))
        )
    return " / ".join(puzzle.spec["solution_route"])


def audit_puzzle(puzzle: Puzzle) -> dict[str, Any]:
    leaked = _normalized_leak(puzzle.answer) in _normalized_leak(
        " ".join([puzzle.question, *puzzle.private_clues["A"], *puzzle.private_clues["B"]])
    )
    common = {
        "puzzle_id": puzzle.id,
        "family": puzzle.family,
        "difficulty": puzzle.difficulty,
        "answer_leakage": leaked,
        "canonical_grade": puzzle.grade(puzzle.answer),
        "equivalent_grade": puzzle.grade(_equivalent_answer(puzzle)),
    }
    if puzzle.family == "logic-grid":
        combined_clues = [*puzzle.clue_specs["A"], *puzzle.clue_specs["B"]]
        combined, nodes = solve_grid(puzzle.spec, combined_clues, limit=2)
        side_a, nodes_a = solve_grid(puzzle.spec, puzzle.clue_specs["A"])
        side_b, nodes_b = solve_grid(puzzle.spec, puzzle.clue_specs["B"])
        removals = []
        for index in range(len(combined_clues)):
            without = combined_clues[:index] + combined_clues[index + 1 :]
            solutions, _ = solve_grid(puzzle.spec, without, limit=2)
            removals.append(len(solutions) >= 2)
        return {
            **common,
            "combined_solutions": len(combined),
            "side_a_solutions": len(side_a),
            "side_b_solutions": len(side_b),
            "clue_minimal": all(removals),
            "solver_nodes": nodes,
            "side_a_solver_nodes": nodes_a,
            "side_b_solver_nodes": nodes_b,
            "passed": (
                len(combined) == 1
                and len(side_a) >= 2
                and len(side_b) >= 2
                and all(removals)
                and not leaked
                and common["canonical_grade"]
                and common["equivalent_grade"]
            ),
        }
    if puzzle.family == "graph-route":
        full, nodes = _all_simple_routes(
            puzzle.spec["nodes"],
            puzzle.spec["edges"],
            puzzle.spec["start"],
            puzzle.spec["end"],
        )
        minimum = full[0][0]
        shortest = [route for route in full if route[0] == minimum]
        side_results: dict[str, dict[str, Any]] = {}
        for side in ("A", "B"):
            routes, side_nodes = _all_simple_routes(
                puzzle.spec["nodes"],
                puzzle.clue_specs[side],
                puzzle.spec["start"],
                puzzle.spec["end"],
            )
            side_results[side] = {
                "reachable": bool(routes),
                "cost": routes[0][0] if routes else None,
                "solver_nodes": side_nodes,
                "insufficient": not routes or routes[0][0] > minimum,
            }
        return {
            **common,
            "combined_shortest_paths": len(shortest),
            "combined_cost": minimum,
            "side_a": side_results["A"],
            "side_b": side_results["B"],
            "solver_nodes": nodes,
            "passed": (
                len(shortest) == 1
                and side_results["A"]["insufficient"]
                and side_results["B"]["insufficient"]
                and not leaked
                and common["canonical_grade"]
                and common["equivalent_grade"]
            ),
        }
    return {**common, "passed": False, "error": "unsupported family"}


def audit_corpus(puzzles: list[Puzzle]) -> dict[str, Any]:
    records = [audit_puzzle(puzzle) for puzzle in puzzles]
    ids_unique = len({puzzle.id for puzzle in puzzles}) == len(puzzles)
    specs_unique = len({digest_value(puzzle.spec) for puzzle in puzzles}) == len(puzzles)
    difficulty_ordered = all(
        DIFFICULTY_RANK[left.difficulty] <= DIFFICULTY_RANK[right.difficulty]
        for left, right in itertools.pairwise(puzzles)
    )
    families = defaultdict(int)
    for puzzle in puzzles:
        families[puzzle.family] += 1
    return {
        "ok": (
            len(puzzles) == 10
            and ids_unique
            and specs_unique
            and difficulty_ordered
            and families["logic-grid"] == 8
            and families["graph-route"] == 2
            and all(record["passed"] for record in records)
        ),
        "puzzle_count": len(puzzles),
        "ids_unique": ids_unique,
        "specs_unique": specs_unique,
        "difficulty_ordered": difficulty_ordered,
        "families": dict(families),
        "records": records,
        "corpus_digest": digest_value([puzzle.model_dump(mode="json") for puzzle in puzzles]),
    }


def validate_corpus(puzzles: list[Puzzle], *, seed: int = 42) -> dict[str, Any]:
    audit = audit_corpus(puzzles)
    regenerated = generate_corpus(seed)
    reproducible = digest_value([p.model_dump(mode="json") for p in regenerated]) == audit[
        "corpus_digest"
    ]
    timeout_verified = False
    try:
        first = puzzles[0]
        solve_grid(
            first.spec,
            [*first.clue_specs["A"], *first.clue_specs["B"]],
            max_nodes=0,
        )
    except SolverTimeout:
        timeout_verified = True
    audit["reproducible"] = reproducible
    audit["timeout_verified"] = timeout_verified
    audit["ok"] = bool(audit["ok"] and reproducible and timeout_verified)
    return audit


def write_corpus(
    puzzles: list[Puzzle],
    corpus_path: Path,
    stats_path: Path,
    *,
    seed: int = 42,
) -> dict[str, Any]:
    report = validate_corpus(puzzles, seed=seed)
    if not report["ok"]:
        raise ValueError("refusing to write a puzzle corpus that failed validation")
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text(
        "\n".join(
            json.dumps(puzzle.model_dump(mode="json"), sort_keys=True) for puzzle in puzzles
        )
        + "\n"
    )
    stats_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    loaded = PuzzleRepository.load(corpus_path)
    if loaded.digest != report["corpus_digest"]:
        raise ValueError("written puzzle corpus failed its digest round trip")
    return report
