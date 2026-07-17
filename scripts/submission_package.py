#!/usr/bin/env python3
"""Build and verify the tracked ZeroHandoff hackathon submission package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUBMISSION = ROOT / "submission"
RUN_ID = "exp04_coveragecanvas_20260717"
TRAINING_ID = "live_train_20260714_v2"
RUN = ROOT / ".zerohandoff" / "runs" / RUN_ID
TRAINING = ROOT / ".zerohandoff" / "training" / TRAINING_ID
APP_SOURCE = RUN / "delivery_bundle.nosync" / "app"
CANONICAL_DEMO = RUN / "delivery_bundle.nosync" / "demo" / "demo.mp4"

EVIDENCE_SOURCES = {
    "training_results.json": TRAINING / "training_results.json",
    "training_metrics.json": TRAINING / "metrics.json",
    "training_manifest.json": TRAINING / "manifest.json",
    "frozen_relationship_vectors.json": ROOT / ".zerohandoff" / "frozen" / "latest.json",
    "run_state.json": RUN / "state.json",
    "run_manifest.json": RUN / "manifest.json",
    "build_request.json": RUN / "build_request.json",
    "experiment_audit.json": ROOT / "experiments" / "audits" / f"{RUN_ID}.json",
    "handoff_rewards.canonical.jsonl": RUN / "logs" / "handoff_rewards.canonical.jsonl",
    "shadow_trust_updates.canonical.jsonl": RUN
    / "logs"
    / "shadow_trust_updates.canonical.jsonl",
    "inference_night_commit.json": RUN / "inference_night_commit.json",
    "inference_relationship_deltas.json": RUN / "inference_relationship_deltas.json",
    "demo_capture_report.json": RUN / "demo" / "capture_report.json",
    "delivery_checksums.json": RUN / "delivery_bundle.nosync" / "checksums.json",
    "delivery_manifest.json": RUN / "delivery_bundle.nosync" / "delivery_manifest.json",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return f"sha256:{value.hexdigest()}"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"available": False, "reason": "ffprobe is not installed"}
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    return {
        "available": True,
        "duration_seconds": round(float(payload["format"]["duration"]), 3),
        "has_video": any(item.get("codec_type") == "video" for item in streams),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        "streams": streams,
    }


def validate_sources() -> dict[str, Any]:
    for path in [APP_SOURCE, CANONICAL_DEMO, *EVIDENCE_SOURCES.values()]:
        require(path.exists(), f"missing canonical source: {path.relative_to(ROOT)}")

    training_state = load_json(TRAINING / "state.json")
    training_metrics = load_json(TRAINING / "metrics.json")
    frozen = load_json(ROOT / ".zerohandoff" / "frozen" / "latest.json")
    run_state = load_json(RUN / "state.json")
    run_manifest = load_json(RUN / "manifest.json")
    audit = load_json(ROOT / "experiments" / "audits" / f"{RUN_ID}.json")
    rewards = load_jsonl(RUN / "logs" / "handoff_rewards.canonical.jsonl")
    shadows = load_jsonl(RUN / "logs" / "shadow_trust_updates.canonical.jsonl")
    night_commits = load_jsonl(RUN / "logs" / "inference_night_commits.jsonl")

    require(training_state.get("status") == "completed", "live training is not completed")
    require(
        training_state.get("completed_rounds") == list(range(1, 11)),
        "live training does not contain all ten rounds",
    )
    require(training_metrics.get("valid_episodes") == 70, "training does not have 70 episodes")
    require(training_metrics.get("rounds_completed") == 10, "training metrics are incomplete")
    require(frozen.get("frozen") is True, "published training snapshot is not frozen")
    require(
        frozen.get("content_digest") == run_state.get("baseline_relationship_digest"),
        "run baseline does not match the immutable training digest",
    )
    require(run_state.get("status") == "completed", "canonical delivery run is incomplete")
    require(
        run_manifest.get("final_outcome") == "delivery_bundle_ready",
        "canonical delivery bundle is not ready",
    )
    require(audit.get("score") == 100.0, "canonical experiment did not score 100")
    require(all(audit.get("trust_invariants", {}).values()), "a trust invariant failed")
    require([row.get("reward") for row in rewards] == [1] * 6, "canonical rewards are invalid")
    require(len(shadows) == 36, "canonical run must contain exactly 36 shadow updates")
    require(len(night_commits) == 1, "canonical run must contain exactly one Night commit")

    media = probe_media(CANONICAL_DEMO)
    if media.get("available"):
        require(media["has_video"] and media["has_audio"], "canonical demo needs audio and video")
        require(media["duration_seconds"] < 180, "canonical demo exceeds three minutes")

    return {
        "training": {
            "run_id": TRAINING_ID,
            "rounds": training_metrics["rounds_completed"],
            "episodes": training_metrics["valid_episodes"],
            "wins": training_metrics["wins"],
            "frozen_digest": frozen["content_digest"],
        },
        "delivery": {
            "run_id": RUN_ID,
            "status": run_state["status"],
            "final_outcome": run_manifest["final_outcome"],
            "audit_score": audit["score"],
            "canonical_handoffs": len(rewards),
            "shadow_updates": len(shadows),
            "night_commits": len(night_commits),
            "learning_commit_sequence": run_state["learning_commit_sequence"],
        },
        "canonical_demo": media,
    }


def build() -> None:
    summary = validate_sources()
    sandbox = SUBMISSION / "sandbox" / "coveragecanvas"
    evidence = SUBMISSION / "evidence"
    media = SUBMISSION / "media"

    if sandbox.exists():
        shutil.rmtree(sandbox)
    if evidence.exists():
        shutil.rmtree(evidence)
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    media.mkdir(parents=True, exist_ok=True)

    shutil.copytree(APP_SOURCE, sandbox)
    shutil.copy2(CANONICAL_DEMO, media / "coveragecanvas-generated-demo.mp4")
    shutil.copy2(RUN / "demo" / "app-preview.png", media / "coveragecanvas-preview.png")
    shutil.copy2(RUN / "demo" / "narration.txt", media / "coveragecanvas-narration.txt")
    for destination, source in EVIDENCE_SOURCES.items():
        shutil.copy2(source, evidence / destination)

    run_manifest = load_json(RUN / "manifest.json")
    entries = []
    manifest_path = evidence / "evidence_manifest.json"
    for path in sorted(item for item in SUBMISSION.rglob("*") if item.is_file()):
        if path == manifest_path:
            continue
        entries.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
        )
    manifest = {
        "schema_version": "1.0",
        "package": "ZeroHandoff OpenAI Build Week submission",
        "canonical_run_id": RUN_ID,
        "assembled_from_completed_run_at": run_manifest["completed_at"],
        "summary": summary,
        "files": entries,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Built submission package with {len(entries)} checksummed files")


def verify() -> None:
    manifest_path = SUBMISSION / "evidence" / "evidence_manifest.json"
    require(manifest_path.exists(), "submission evidence manifest is missing; run build first")
    manifest = load_json(manifest_path)
    for row in manifest.get("files", []):
        path = ROOT / row["path"]
        require(path.is_file(), f"package file is missing: {row['path']}")
        require(path.stat().st_size == row["bytes"], f"size mismatch: {row['path']}")
        require(digest(path) == row["sha256"], f"checksum mismatch: {row['path']}")

    evidence = SUBMISSION / "evidence"
    metrics = load_json(evidence / "training_metrics.json")
    audit = load_json(evidence / "experiment_audit.json")
    state = load_json(evidence / "run_state.json")
    require(metrics.get("rounds_completed") == 10, "packaged training metrics are incomplete")
    require(metrics.get("valid_episodes") == 70, "packaged training episodes are incomplete")
    require(state.get("status") == "completed", "packaged canonical run is incomplete")
    require(audit.get("score") == 100.0, "packaged experiment audit is not 100")
    require(all(audit.get("trust_invariants", {}).values()), "packaged trust invariant failed")
    require((SUBMISSION / "sandbox" / "coveragecanvas" / "dist" / "index.html").is_file(), "prebuilt sandbox is missing")

    videos = sorted((SUBMISSION / "media").glob("*.mp4"))
    require(videos, "submission contains no MP4 demo")
    for video in videos:
        media = probe_media(video)
        if media.get("available"):
            require(media["has_video"] and media["has_audio"], f"invalid media streams: {video.name}")
            require(media["duration_seconds"] < 180, f"video exceeds three minutes: {video.name}")
    print(f"Verified {len(manifest.get('files', []))} package files and {len(videos)} narrated videos")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "verify"))
    args = parser.parse_args()
    if args.command == "build":
        build()
    else:
        verify()


if __name__ == "__main__":
    main()
