---
name: pipeline-status
description: Inspect and summarize ZeroHandoff training or delivery state, stages, trust digest, agent calls, gates, repairs, artifacts, logs, demo, and bundle evidence without changing the run. Use when the user asks for progress, status, evidence, failures, logs, the current stage, or where to view a run.
---

# Pipeline Status

Provide an evidence-backed status report from persisted run state and append-only logs. This skill is read-only except when the user explicitly asks to start the local Control Room.

## Workflow

1. Identify the requested run ID; when none is provided, use the most recently updated run of the relevant type.
2. For delivery, invoke `PYTHONPATH=src python3 -m zerohandoff.cli --repo . status [run_id]` and inspect the matching `state.json`, `manifest.json`, and JSONL logs when more detail is needed.
3. For training, inspect `.zerohandoff/training/<run_id>/state.json`, `metrics.json`, `manifest.json`, and focused logs. Do not infer completion from directory existence.
4. Report status, current and completed stages or rounds, agent-call count, gate and repair outcomes, immutable baseline digest, run-start inference digest, handoff rewards, shadow deltas, night-commit sequence, failure reason, and whether preview, video, and bundle evidence exist.
5. Distinguish a model/content failure from an infrastructure failure and identify the last committed resumable boundary.
6. When requested, start the local Control Room with the internal `serve` command and provide its URL. Do not alter stage or gate state from the UI.

## Reporting

Lead with the outcome in a short paragraph. Include exact run IDs and repository-relative evidence paths. Never claim a run passed unless state, manifest, artifacts, checksums, and required media evidence agree.
