# ZeroHandoff judge guide

ZeroHandoff is a Codex-first developer tool that turns one Build Request into a
tested React application, an auditable evidence bundle, and a narrated browser
demo. The repository ships both the orchestration engine and a prebuilt product
created by one real autonomous run.

## Supported platforms

- **Full ZeroHandoff engine:** macOS or Linux, Python 3.11+, Node.js 22+, npm,
  Chrome/Chromium, FFmpeg, and Codex CLI 0.144.0+ with GPT-5.6 Sol access.
- **Prebuilt judge sandbox:** any desktop platform with a modern browser and a
  local static-file server. It has no backend, account, or network dependency.
- **Reference runtime:** Codex. Future host adapters are intentionally out of
  scope for this hackathon build.

## Fastest test — no install or rebuild

Serve the product created by the canonical autonomous run:

```bash
python3 -m http.server 8000 --directory submission/sandbox/coveragecanvas/dist
```

Open `http://127.0.0.1:8000`. In CoverageCanvas:

1. Open **Morning · North · Safety inspection**.
2. Attempt to assign **Theo Brooks** and inspect the two specific blockers.
3. Assign **Amara Cole** and confirm coverage changes from **7/8** to **8/8**.
4. Use **Undo**, then **Reset plan**, and confirm deterministic recovery.

The same real run's 54-second generated browser demo is at
`submission/media/coveragecanvas-generated-demo.mp4`.

## Verify the submitted evidence

This checks every packaged file hash, the 10-round/70-episode training summary,
the 100/100 canonical run audit, all trust invariants, the prebuilt sandbox, and
the audio/video duration gates:

```bash
python3 scripts/submission_package.py verify
```

Representative evidence is under `submission/evidence/`. The immutable trained
baseline remains under `.zerohandoff/frozen/latest.json`; versioned inference
learning state remains under `.zerohandoff/learning/`.

## Run the engine without model calls

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m zerohandoff.cli --repo . puzzles validate
.venv/bin/python -m zerohandoff.cli --repo . train --adapter fixture --rounds 10
.venv/bin/python -m zerohandoff.cli --repo . run \
  --request tests/fixtures/build_request.json --adapter fixture
```

The fixture adapter exercises orchestration, persistence, gates, repair,
training, artifact handoffs, demo assembly, and bundle creation without making
agent calls. Fixture vectors are test evidence, never production trust.

## Run the real Codex pipeline

Open this repository in Codex and ask:

```text
$run-pipeline
Build a small local React application for [audience] that [desired outcome].
Constraints: [non-negotiable boundaries].
```

The checked-in skill collects only the Build Request, validates the published
training baseline, and invokes the GPT-5.6 Sol pipeline. Judges do **not** need
to retrain. Use `$pipeline-status` to inspect persisted stages, calls, repairs,
trust lineage, artifacts, logs, video, and bundle evidence.

For internal commands, Control Room setup, and recovery behavior, see
[`RUNBOOK.md`](RUNBOOK.md).

## Canonical evidence

- Training: `live_train_20260714_v2` — 10 rounds, 70 valid episodes, 63 solved,
  280 accepted solver calls, 140 accepted Curator calls.
- Immutable baseline: `sha256:b0849f195010cbedca942a351f6005dc8e91959b8f276ab4dec86bfd9baef324`.
- Delivery: `exp04_coveragecanvas_20260717` — all seven lifecycle stages, demo,
  and bundle completed; independent experiment score 100/100.
- Continual learning: six canonical handoff rewards, 36 shadow trust updates,
  one Night commit, memory updated for all 14 agents, and no change to the nine
  frozen non-trust dimensions or the training source.
