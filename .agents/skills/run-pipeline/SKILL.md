---
name: run-pipeline
description: Collect one ZeroHandoff Build Request and start a complete autonomous delivery run through SENSE, MODEL, COMPOSE, DECIDE, SIMULATE, EXECUTE, OBSERVE, demo, and bundle using the trained trust baseline. Use when the user asks to build an application, start the pipeline, run ZeroHandoff, or produce a delivery bundle. Do not retrain trust unless the user separately requests training.
---

# Run Pipeline

Turn one short outcome-oriented request into a runnable, tested React/Vite application, full evidence trail, and narrated video without asking for product decisions after execution starts.

## Workflow

1. Read `AGENTS.md`, `pipeline/intake_template.md`, and the current settings. Preserve the permanent `context/` protection rule.
2. Collect only the Build Request: idea, audience, desired outcome, and non-negotiable constraints. Optional capabilities, assets, visual direction, exclusions, and demo preferences may be included when already known.
3. Call the user's input only a Build Request. SENSE produces the Opportunity Model as the first pipeline artifact.
4. Run the internal doctor with its live GPT-5.6 Sol probe and verify `.zerohandoff/frozen/latest.json` is valid before starting. Also verify the separate inference state belongs to that baseline. Judges should not need to repeat training.
5. Persist the request as structured JSON outside `context/`, then invoke the internal engine yourself with `PYTHONPATH=src python3 -m zerohandoff.cli --repo . run --request <path> --adapter codex`. Use `fixture` only for an explicitly requested smoke or test run.
6. Start or reuse the local Control Room when visual progress is useful. The UI observes server state; it never chooses stages, leads, gates, or repairs.
7. Continue autonomously through all seven cells. Each next cell accepts the prior handoff (`1`) or requests one bounded revision (`0`). Accumulate trust changes in shadow only; never change prompt behavior mid-run.
8. After the demo passes, run exactly one extra-high-reasoning inference Night Curator call, commit slow trust and fast memory, and preserve start/end/delta evidence. An exhausted repair or invalid night commit fails closed.
9. On completion, verify the bundle manifest and checksums, actual app preview, tests and gates, append-only JSON logs, immutable baseline digest, inference lineage, and narrated video.
10. Return the run ID, outcome, Control Room location, bundle path, video path, learning commit, and any failed-closed blocker.

## Delivery order

SENSE Opportunity Model → MODEL Outcome Model → COMPOSE Capability Graph → DECIDE Decision Graph → SIMULATE Scenario Model → EXECUTE Autonomous Change → OBSERVE Evidence + Learning → narrated demo → Delivery Bundle.

## Safety

- Local output only; do not deploy, publish, purchase, or contact people.
- Never rewrite the frozen training vectors. Inference learns only in the separate state under `.zerohandoff/learning/`.
- Preserve isolated first judgments for both agents in every cell.
- Record calls, lead selection, gates, repairs, artifacts, commands, demo evidence, and final outcome in JSONL.
