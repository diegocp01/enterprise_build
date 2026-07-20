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
python3 -m http.server 8000 --directory submission/sandbox/echoledger/dist
```

Open `http://127.0.0.1:8000`. In EchoLedger:

1. Select **Complaint · 02:31** and confirm the transcript synchronizes to the
   exact speaker-labelled segment.
2. Use **Redact presentation** on evidence `EV-015`; its stable evidence identity
   and decision history remain visible.
3. Inspect the recurring export-stall signal and its three exact evidence links.
4. Assign the action to **Product · Priya Nolen**, export the local case brief,
   then use **Reset fictional experience** to restore the deterministic baseline.

The same real run's 87-second generated browser demo is at
`submission/media/echoledger-generated-demo.mp4`.

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
- Delivery: `step13_echoledger_live_20260718` — all seven lifecycle stages,
  demo, and 147-file bundle completed; independent experiment score 100/100.
- Continual learning: the canonical rewards were `[1, 1, 1, 1, 0, 1]`; the
  rejected handoff was repaired and preserved instead of rewritten as success.
  Exactly 36 shadow trust updates and one Night commit produced sequence 6,
  memory changed for all 14 agents, and neither the nine frozen non-trust
  dimensions nor the immutable training source changed.
