---
name: train-trust
description: Run or resume ZeroHandoff's ten-round Trust MoA training, audit the 420-call baseline and JSON evidence, and publish the immutable directed relationship-vector snapshot. Use when the user asks to train the seven agent pairs, run the puzzles, resume training, inspect training progress, or freeze trust. Do not use for a normal delivery run with already-frozen vectors.
---

# Train Trust

Train all seven two-agent cells through the repository's deterministic day/night protocol without requiring the user to use a terminal.

## Workflow

1. Read `AGENTS.md`, `settings/*.json`, and the protected source `context/trust_moa.md`. Never edit a non-HTML file under `context/`.
2. Run `PYTHONPATH=src python3 -m zerohandoff.cli --repo . doctor --live`. Stop before model work if any required runtime check or the schema-constrained GPT-5.6 Sol probe fails.
3. Confirm the puzzle corpus and settings digests that will be used. Do not silently change either during a resumable run.
4. For a smoke request, run the fixture adapter. For actual training, use the Codex adapter. Never present fixture vectors as trained production vectors.
5. Invoke `PYTHONPATH=src python3 -m zerohandoff.cli --repo . train --adapter <fixture|codex> --rounds 10 [--run-id <id>]` yourself; do not ask the user to paste the command.
6. Verify 70 completed episodes, 280 task-solving calls, 140 single-prompt curator calls, and 420 total baseline calls before retries. Backend errors must not update reward, trust, or difficulty.
7. Verify `frozen_relationship_vectors.json` against its digest and confirm `.zerohandoff/frozen/latest.json` was published. Never edit a frozen vector by hand.
8. Report the run ID, completion counts, retries/errors, frozen digest, and evidence paths concisely.

## Invariants

- Each agent sends exactly one initial and one revised task message per episode.
- Trust is updated mechanically; curators may change only the nine non-trust dimensions and memory.
- Ties resolve by higher inbound trust, then Dominance, then last speaker.
- Training is chat-driven and has no dedicated UI.
- Every material action and outcome remains in append-only JSONL evidence.
