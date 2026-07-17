# Inference experiment scorecard

The campaign originally targeted ten runs. On 2026-07-17 the user approved a
scope reduction: recover Experiment 3, run exactly one additional experiment,
then stop. Experiments 5–10 are cancelled, not incomplete.

Every experiment is audited after completion and before the next run. Ratings are
`0` missing or wrong, `1` major gaps, `2` partial, `3` complete and reproducible,
and `4` exceptional with strong edge-case handling.

| Area | Weight | Required evidence |
|---|---:|---|
| SENSE | 4 | Request fidelity, boundaries, assumptions, observable success |
| MODEL | 6 | Traceable IDs, exact semantics, acceptance checks, edge cases |
| COMPOSE | 6 | Complete journey, states, responsiveness, accessibility |
| DECIDE | 5 | Minimal architecture, data model, risks, commands, coverage |
| SIMULATE | 6 | Valid dependencies, measurable done conditions, verification |
| EXECUTE | 16 | Feature completeness, tests, typecheck, build, bounded repair |
| OBSERVE | 8 | Reproducible contract proof and honest defects |
| Application | 14 | End-to-end behavior, polish, accessibility, reset, no console errors |
| Demo | 9 | Real input-to-output actions, visible changes, synchronized narration |
| Logs and bundle | 6 | Canonical events, repair provenance, checksums, reproducibility |
| Continual memory | 10 | Relevant lessons retrieved, applied, and evidence-backed |
| Architecture improvement | 10 | General fix, regression test, measured gain, trust untouched |

Hard gates: all deterministic gates pass; no unresolved high/critical defect;
all must-have outcomes have evidence; tests and production build pass; no runtime
network request; the demo contains real interactions and distinct states; bundle
checksums verify; and every trust invariant passes.

Before score 95, a run must meet its floor and improve by at least one point over
the preceding run. At or above 95, it must remain at least 95 with no category
regression and improve a measurable efficiency or quality dimension.

## Immutable trust invariants

- The published training snapshot and digest remain byte-for-byte unchanged.
- Each run starts from the immediately preceding successful inference commit.
- Run-start relationships stay fixed throughout that run.
- Only trust changes during inference; the other nine dimensions stay frozen.
- Existing alpha, RPE, clamp, reward, shadow-update, and Night-commit rules do not change.
- Six canonical handoff rewards and 36 canonical shadow updates are expected per complete run.
- Exactly one Night Curator commit occurs after a completed prototype and passing demo.
- Audit or architecture work cannot issue rewards or trigger a Night commit.
- Any failure halts the campaign and is reported instead of changing trust mechanics.

## Completed results

| Experiment | Result | Learning commit | Handoff rewards | Evidence |
|---|---:|---:|---|---|
| RoomReady | 90/100 | 2 | 6 accepted | Original campaign report |
| StockSignal | 92/100 (automated 100) | 3 | 6 accepted | Original campaign report |
| PolicyProof (corrected) | 100/100 | 4 | `1,1,1,1,0,1` | Rejection remained 0 after revision; stale original commit archived |
| CoverageCanvas | 100/100 | 5 | `1,1,1,1,1,1` | 54.04s interactive demo; 117-file verified bundle |

The final two independent audits report unchanged trust code and frozen training
hashes, exactly one Night commit per completed prototype, 36 canonical shadow
updates, current artifact lineage, and zero changes to the nine non-trust
relationship dimensions.
