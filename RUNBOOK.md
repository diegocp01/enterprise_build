# ZeroHandoff local runbook

ZeroHandoff is designed to be started from Codex chat through `$train-trust`,
`$run-pipeline`, and `$pipeline-status`. The commands below are the internal
engine surface used by those skills and automated tests, not the public UX.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cd ui && npm install && npm run build && cd ..
```

The hackathon runtime is Codex CLI 0.144.0 or newer with access to
`gpt-5.6-sol`. Validate tools, authentication, strict project config,
structured output, and live model access:

```bash
.venv/bin/python -m zerohandoff.cli --repo . doctor --live
```

## Agent-free smoke path

```bash
.venv/bin/python -m zerohandoff.cli --repo . train --adapter fixture --rounds 10
.venv/bin/python -m zerohandoff.cli --repo . run \
  --request tests/fixtures/build_request.json --adapter fixture
```

Fixture vectors and artifacts are test evidence only. Real training and
delivery use `--adapter codex`; the chat skills select that automatically.

Validate the committed real puzzle corpus before training:

```bash
.venv/bin/python -m zerohandoff.cli --repo . puzzles validate
```

## Control Room

```bash
.venv/bin/python -m zerohandoff.cli --repo . serve --port 8765
```

Open `http://127.0.0.1:8765`. Training intentionally remains chat-only. The
Control Room observes delivery state via the local API and never decides a
stage transition, lead, gate, or repair.

## Evidence and recovery

- Training: `.zerohandoff/training/<run_id>/`
- Frozen vectors: `.zerohandoff/frozen/latest.json`
- Current inference relationships and memory: `.zerohandoff/learning/inference_relationships.json`
- Versioned inference night commits: `.zerohandoff/learning/commits/` and `commits.jsonl`
- Delivery: `.zerohandoff/runs/<run_id>/`
- Canonical stream: `events.jsonl`
- Focused evidence: `logs/*.jsonl`
- Compiled non-trust guidance: `logs/relationship_policies.jsonl`
- Inference rewards and shadow learning: `logs/handoff_rewards.jsonl` and `logs/shadow_trust_updates.jsonl`
- Training summary tables: `.zerohandoff/training/<run_id>/training_results.json`
- Resume: rerun the same delivery ID with `--resume`
- Delivery output: `delivery_bundle/` and `delivery_bundle.zip`

Run `pytest -q` for the full agent-free contract and integration suite. Run
`npm run typecheck && npm run build` in `ui/` for the Control Room.
