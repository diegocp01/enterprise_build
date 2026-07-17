#!/usr/bin/env python3
"""Audit one ZeroHandoff inference experiment without changing run state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


STAGES = ("SENSE", "MODEL", "COMPOSE", "DECIDE", "SIMULATE", "EXECUTE", "OBSERVE")
NON_TRUST_DIMENSIONS = (
    "affection",
    "confidence",
    "dominance",
    "openness",
    "patience",
    "reliability",
    "resentment",
    "respect",
    "warmth",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_descends_from(
    artifacts: list[dict[str, Any]],
    consumer_digest: str,
    producer_digest: str,
) -> bool:
    ancestry = {
        str(row["content_digest"]): str(
            row.get("input_digests", {}).get("previous", "")
        )
        for row in artifacts
        if row.get("gate_status") == "PASS" and row.get("content_digest")
    }
    current = consumer_digest
    visited: set[str] = set()
    while current and current not in visited:
        if current == producer_digest:
            return True
        visited.add(current)
        current = ancestry.get(current, "")
    return False


def command_kind(row: dict[str, Any]) -> str | None:
    command = str(row.get("command", ""))
    if command.startswith("npm install") or command.startswith("npm ci"):
        return "install"
    if command == "npm test" or "vitest" in command or "node --test" in command:
        return "test"
    if "typecheck" in command or "tsc" in command and "build" not in command:
        return "typecheck"
    if command == "npm run build" or "vite build" in command:
        return "build"
    return None


def command_reported_error(row: dict[str, Any]) -> bool:
    """Recognize both current structured errors and older logged error markers."""

    if row.get("reported_error"):
        return True
    output = f"{row.get('stdout_tail', '')}\n{row.get('stderr_tail', '')}"
    return bool(re.search(r"(?m)^### Error[ \t]*\r?$", output))


def latest_demo_evidence(run_root: Path) -> dict[str, Any]:
    artifacts = load_jsonl(run_root / "logs" / "artifacts.jsonl")
    demos = [row for row in artifacts if row.get("stage") == "DEMO"]
    if not demos:
        return {}
    latest = max(demos, key=lambda row: int(row.get("version", 0)))
    evidence_file = next(
        (run_root / relative for relative in latest.get("content_files", []) if relative.endswith("demo_evidence.json")),
        None,
    )
    return load_json(evidence_file) if evidence_file and evidence_file.is_file() else {}


def verify_bundle(run_root: Path) -> tuple[int, list[str]]:
    preferred = run_root / "delivery_bundle.nosync"
    bundle = preferred if preferred.is_dir() else run_root / "delivery_bundle"
    checksums_path = bundle / "checksums.json"
    if not checksums_path.is_file():
        return 0, [f"{bundle.name}/checksums.json missing"]
    checksums = load_json(checksums_path)
    failures = []
    for relative, expected in checksums.items():
        path = bundle / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
        elif digest(path) != expected:
            failures.append(f"digest:{relative}")
    physical_files = {
        str(path.relative_to(bundle))
        for path in bundle.rglob("*")
        if path.is_file()
    }
    expected_files = set(checksums) | {"checksums.json", "delivery_manifest.json"}
    failures.extend(f"unchecksummed:{relative}" for relative in sorted(physical_files - expected_files))
    failures.extend(f"unmaterialized:{relative}" for relative in sorted(expected_files - physical_files))
    return len(checksums), failures


def audit(repo: Path, run_id: str) -> dict[str, Any]:
    run_root = repo / ".zerohandoff" / "runs" / run_id
    if not run_root.is_dir():
        raise FileNotFoundError(run_root)
    state = load_json(run_root / "state.json")
    manifest = load_json(run_root / "manifest.json")
    frozen = load_json(repo / ".zerohandoff" / "frozen" / "latest.json")
    campaign = load_json(repo / "experiments" / "campaign.json")
    start = load_json(run_root / "inference_relationships.start.json")
    end_path = run_root / "inference_relationships.end.json"
    night_path = run_root / "inference_night_commit.json"
    end = load_json(end_path) if end_path.is_file() else start
    night = load_json(night_path) if night_path.is_file() else {}
    artifacts = load_jsonl(run_root / "logs" / "artifacts.jsonl")
    gates = load_jsonl(run_root / "logs" / "gates.jsonl")
    repairs = load_jsonl(run_root / "logs" / "repairs.jsonl")
    commands = load_jsonl(run_root / "logs" / "commands.jsonl")
    canonical_handoffs = run_root / "logs" / "handoff_rewards.canonical.jsonl"
    canonical_shadows = run_root / "logs" / "shadow_trust_updates.canonical.jsonl"
    handoffs = load_jsonl(
        canonical_handoffs
        if canonical_handoffs.is_file()
        else run_root / "logs" / "handoff_rewards.jsonl"
    )
    shadows = load_jsonl(
        canonical_shadows
        if canonical_shadows.is_file()
        else run_root / "logs" / "shadow_trust_updates.jsonl"
    )
    completed = state.get("status") == "completed"

    stage_evidence: dict[str, Any] = {}
    latest_artifacts: dict[str, dict[str, Any]] = {}
    for stage in STAGES:
        rows = [row for row in artifacts if row.get("stage") == stage]
        latest = max(rows, key=lambda row: int(row.get("version", 0))) if rows else None
        if latest:
            latest_artifacts[stage] = latest
        stage_evidence[stage] = {
            "artifact_present": bool(latest),
            "latest_version": int(latest.get("version", 0)) if latest else 0,
            "latest_gate": latest.get("gate_status") if latest else None,
            "files_present": bool(latest)
            and all((run_root / relative).is_file() for relative in latest.get("content_files", [])),
            "repairs": len([row for row in repairs if row.get("stage") == stage]),
        }

    non_trust_changes: list[str] = []
    trust_changes = 0
    for edge_id, start_edge in start["vectors"].items():
        before = start_edge["vector"]
        after = end["vectors"][edge_id]["vector"]
        if before.get("trust") != after.get("trust"):
            trust_changes += 1
        for dimension in NON_TRUST_DIMENSIONS:
            if before.get(dimension) != after.get(dimension):
                non_trust_changes.append(f"{edge_id}:{dimension}")

    start_memory_lines = sum(len(lines) for lines in start.get("memories", {}).values())
    end_memory_lines = sum(len(lines) for lines in end.get("memories", {}).values())
    memory_agents_changed = sum(
        start.get("memories", {}).get(agent) != lines
        for agent, lines in end.get("memories", {}).items()
    )
    demo = latest_demo_evidence(run_root)
    bundle_files, bundle_failures = verify_bundle(run_root)
    failed_commands = [
        row
        for row in commands
        if row.get("exit_code") not in (None, 0)
        or row.get("status") in {"failed", "timeout"}
        or command_reported_error(row)
    ]
    latest_commands: dict[str, dict[str, Any]] = {}
    for row in commands:
        kind = command_kind(row)
        if kind:
            latest_commands[kind] = row
    required_command_kinds = {"install", "test", "typecheck", "build"}
    final_commands_pass = required_command_kinds == set(latest_commands) and all(
        row.get("exit_code") == 0
        and not row.get("timed_out")
        and not command_reported_error(row)
        and row.get("status") not in {"failed", "timeout"}
        for row in latest_commands.values()
    )
    stage_passes = sum(
        item["artifact_present"] and item["files_present"] and item["latest_gate"] == "PASS"
        for item in stage_evidence.values()
    )
    expected_hashes = campaign.get("trust_baseline", {}).get(
        "algorithm_file_hashes", {}
    )
    actual_hashes = {
        relative: hashlib.sha256((repo / relative).read_bytes()).hexdigest()
        for relative in expected_hashes
        if (repo / relative).is_file()
    }
    expected_transitions = list(zip(STAGES, STAGES[1:]))
    handoff_map = {
        (str(row.get("producer_stage")), str(row.get("consumer_stage"))): row
        for row in handoffs
    }
    handoff_lineage_current = set(handoff_map) == set(expected_transitions)
    shadow_lineage_current = len(shadows) == 36
    for producer_stage, consumer_stage in expected_transitions:
        producer = latest_artifacts.get(producer_stage, {})
        consumer = latest_artifacts.get(consumer_stage, {})
        handoff = handoff_map.get((producer_stage, consumer_stage), {})
        transition_shadows = [
            row
            for row in shadows
            if row.get("producer_stage") == producer_stage
            and row.get("consumer_stage") == consumer_stage
        ]
        handoff_lineage_current = handoff_lineage_current and all(
            (
                bool(producer),
                bool(consumer),
                handoff.get("producer_artifact_digest")
                == producer.get("content_digest"),
                set(handoff.get("evidence", []))
                == set(producer.get("content_files", [])),
                artifact_descends_from(
                    artifacts,
                    str(consumer.get("content_digest", "")),
                    str(producer.get("content_digest", "")),
                ),
            )
        )
        shadow_lineage_current = shadow_lineage_current and len(
            transition_shadows
        ) == 6 and all(
            row.get("reward") == handoff.get("reward")
            and set(row.get("evidence", [])) == set(handoff.get("evidence", []))
            for row in transition_shadows
        )
    trust_invariants = {
        "algorithm_and_frozen_hashes_unchanged": actual_hashes == expected_hashes,
        "frozen_baseline_digest_matches": (
            start.get("baseline_relationship_digest") == frozen.get("content_digest")
            and end.get("baseline_relationship_digest") == frozen.get("content_digest")
        ),
        "lineage_unchanged": start.get("lineage_id") == end.get("lineage_id"),
        "commit_incremented_once": completed
        and end.get("commit_sequence") == start.get("commit_sequence", -1) + 1,
        "incomplete_run_has_no_commit": completed
        or end.get("commit_sequence") == start.get("commit_sequence"),
        "non_trust_dimensions_frozen": not non_trust_changes,
        "night_commit_approved": completed
        and bool(night.get("night_workspace", {}).get("trust_commit_approved")),
        "canonical_handoffs_six": len(handoffs) == 6,
        "canonical_shadow_updates_36": len(shadows) == 36,
        "final_handoff_artifact_lineage_current": handoff_lineage_current,
        "shadow_learning_lineage_current": shadow_lineage_current,
        "incomplete_shadow_updates_consistent": completed
        or len(shadows) == 6 * len(handoffs),
        "all_trust_steps_within_cap": all(
            abs(float(row.get("proposed_delta", row.get("delta", 0.0)))) <= 0.1 + 1e-12
            for row in shadows
        ),
    }
    app_signals = {
        "recorded_commands": len(commands),
        "failed_commands": len(failed_commands),
        "latest_validation_commands": sorted(latest_commands),
        "final_validation_passed": final_commands_pass,
        "build_evidence_present": any(row.get("stage") == "EXECUTE" for row in gates),
    }
    narration_provider = str(demo.get("narration_provider", ""))
    demo_signals = {
        "capture_mode": demo.get("capture_mode"),
        "duration_seconds": demo.get("duration_seconds"),
        "has_video": bool(demo.get("has_video")),
        "has_audio": bool(demo.get("has_audio")),
        "has_motion": bool(demo.get("has_motion")),
        "narration_provider": narration_provider,
        "neural_narration": narration_provider.startswith(
            ("edge-neural:", "supplied-neural:")
        ),
        "presenter_quality_narration": narration_provider.startswith(
            ("edge-neural:", "supplied-neural:", "macos-enhanced:")
        ),
        "visual_content": bool(demo.get("visual_content")),
    }

    score_components = {
        "seven_stage_artifacts": round(25 * stage_passes / len(STAGES), 2),
        "run_completion": 10.0 if state.get("status") == "completed" else 0.0,
        "deterministic_commands": 15.0 if final_commands_pass else 0.0,
        "interactive_demo": 15.0
        if all(
            (
                demo_signals["capture_mode"] == "browser-interactive",
                demo_signals["has_video"],
                demo_signals["has_audio"],
                demo_signals["has_motion"],
                demo_signals["visual_content"],
            )
        )
        else 0.0,
        "bundle_integrity": 10.0 if bundle_files and not bundle_failures else 0.0,
        "trust_invariants": 15.0
        if all(trust_invariants.values())
        else 0.0,
        "continual_memory": 10.0 if end_memory_lines and memory_agents_changed else 0.0,
    }
    report = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": state.get("status"),
        "final_outcome": manifest.get("final_outcome"),
        "score": round(sum(score_components.values()), 2),
        "score_components": score_components,
        "stages": stage_evidence,
        "repairs_total": len(repairs),
        "app": app_signals,
        "demo": demo_signals,
        "bundle": {"files": bundle_files, "failures": bundle_failures},
        "continual_learning": {
            "start_commit_sequence": start.get("commit_sequence"),
            "end_commit_sequence": end.get("commit_sequence"),
            "start_digest": start.get("content_digest"),
            "end_digest": end.get("content_digest"),
            "trust_edges_changed": trust_changes,
            "memory_lines_before": start_memory_lines,
            "memory_lines_after": end_memory_lines,
            "memory_agents_changed": memory_agents_changed,
            "handoff_rewards": [row.get("reward") for row in handoffs],
        },
        "trust_invariants": trust_invariants,
        "non_trust_changes": non_trust_changes,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.repo.resolve(), args.run_id)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if report["status"] == "completed" and all(report["trust_invariants"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
