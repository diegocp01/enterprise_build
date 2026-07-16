# ZeroHandoff Execution Plan

Status: approved hackathon route

Decision date: 2026-07-14

System design: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)

Status tracker: [PROJECT_PLAN.json](PROJECT_PLAN.json)

## 1. Fixed hackathon decisions

- Codex with GPT-5.6 is the only supported runtime.
- The human interacts through Codex chat, not a ZeroHandoff command-line UI.
- Repository skills expose `$train-trust`, `$run-pipeline`, and
  `$pipeline-status`.
- Fourteen project-scoped custom agents live under `.codex/agents/`.
- Training is chat-controlled and has no browser UI.
- The delivery pipeline is UI-first: its local Control Room visualizes the
  autonomous run, evidence, finished app, and narrated demo.
- Training covers seven fixed pairs and ten puzzles. After all seven episodes in
  a round finish, each agent receives one single-prompt Night Curator update.
  The curator handles memory and the nine non-trust dimensions; trust moves only
  through the mechanical RPE rule. A deterministic policy compiler later turns
  at most three strong non-trust dimensions into qualitative collaboration
  guidance; trust remains the only authority signal.
- The 14 final relationship vectors are frozen, hashed, and bundled before any
  live delivery inference.
- The live delivery target is one small local React + TypeScript + Vite web app
  for a fictional non-banking enterprise, with no external services.
- Judges receive the frozen vectors and training evidence. They invoke a fresh
  end-to-end delivery run from Codex chat; they do not repeat training.
- GitHub Copilot, Claude Code, public CLI UX, and non-web product types are
  deferred until after the hackathon.

## 2. Execution rule

Build only what proves this sequence:

```text
Codex-native repo configuration
→ deterministic fixture proof
→ 7 pairs × 10 real puzzle rounds
→ validate and freeze 14 relationship vectors
→ one real autonomous delivery run
→ runnable React/Vite app + JSON evidence + narrated demo
```

Every milestone ends with executable evidence. Live training cannot begin until
the puzzle corpus, resumable training state, curator schema, and freeze path pass
with fixtures. Live delivery cannot begin until the frozen-vector digest and the
fixture delivery path pass.

Internal scripts and `codex exec` may provide deterministic execution beneath
the product. They are implementation details invoked by Codex skills, not steps
the user or judges must operate manually.

## 3. Implementation milestones

### M0 — Codex runtime spike and repository shell

Deliver:

- Python project and React/Vite workspace with reproducible checks;
- `AGENTS.md` repository rules;
- trusted-project `.codex/config.toml`;
- one minimal custom agent under `.codex/agents/`;
- skeleton repo skills under `.agents/skills/`;
- Codex readiness check for installed version, authentication, GPT-5.6 access,
  schema-constrained output, sandbox behavior, and workspace writes; and
- safe generated-output and secret rules.

Acceptance:

- Codex discovers the project agent and all three skills;
- one schema-constrained read-only call succeeds and is logged as JSONL;
- one sandboxed workspace call writes only inside its assigned fixture folder;
- missing credentials or model access fail before a workload starts; and
- no tool writes to protected non-HTML files under `context/`.

### M1 — Contracts, evidence, and frozen-trust core

Deliver:

- Pydantic models and JSON Schemas for training episodes, curator updates,
  relationship vectors, Build Requests, artifacts, gates, repairs, run state,
  proof entries, and demo plans;
- append-only `events.jsonl` plus focused call, artifact, gate, repair, command,
  training, relationship-policy, and demo logs;
- atomic checkpoints, immutable artifact versions, manifests, and SHA-256
  digests;
- secret redaction and structured errors; and
- frozen-vector loader and mutation guard.

Acceptance:

- fixtures validate byte-stably against exported schemas;
- invalid agent or curator output commits no partial state;
- event replay reconstructs the last committed stage boundary;
- tampering with an artifact or frozen vector is detected; and
- every event carries run, actor, model, timestamp, vector digest, inputs,
  outputs, status, usage when available, and errors.

Depends on: M0.

### M2 — Puzzle corpus and resumable training engine

Deliver:

- ten deterministic, solver-backed puzzles with private clues and grading;
- fixed registry of seven pairs and 14 identities;
- resumable 10-round training state machine;
- isolated contributions from both agents in every pair attempt;
- exactly two task-solving messages per agent in every episode;
- mechanical directed-trust updates after the seven episodes in each round;
- one single-prompt Night Curator call per agent after every completed round;
- reward, memory, and directed 10-dimensional relationship-vector updates;
- retry and failure policy; and
- freeze operation producing 14 vectors, a manifest, and one digest.

Acceptance:

- all puzzles pass correctness, uniqueness, leakage, and reproducibility audits;
- a fixture run completes all 70 team episodes;
- the expected baseline is 280 day-agent calls plus 140 curator calls—420 model
  invocations before retries;
- interruption after any episode resumes without duplicating a committed
  attempt or update;
- each directed edge updates independently; and
- frozen outputs are immutable and loadable by delivery.

Depends on: M1.

### M3 — Delivery orchestrator, pairs, gates, and repair

Deliver:

- deterministic stage machine for SENSE → MODEL → COMPOSE → DECIDE → SIMULATE → EXECUTE
  → OBSERVE → demo;
- two isolated initial proposals per delivery cell;
- integration, peer review, deterministic gates, and one bounded repair route;
- Outcome Model item IDs propagated through every downstream artifact;
- frozen-trust lead selection and qualitative stance derivation; and
- stage-boundary resume and immutable artifact history.

The lead rule is exact:

1. Higher inbound trust wins.
2. If tied, higher personality **Dominance** wins.
3. If still tied, the agent that **spoke last** wins.
4. Stable hashes, alphabetical identity, and agent IDs never break trust ties.

Acceptance:

- a fixture Build Request reaches a complete Delivery Bundle;
- A and B never see each other's initial proposal;
- every lead decision records trust, Dominance when needed, last-speaker state
  when needed, selection reason, and vector digest;
- no stage advances without a committed passing gate;
- one deliberately broken artifact demonstrates repair and preserves both
  versions; and
- exhausted repair budget fails closed without asking the human to decide.

Depends on: M1.

### M4 — Codex agent team and live adapter

Deliver:

- 14 original project-scoped agent files in `.codex/agents/`, two per delivery
  responsibility;
- distinct names, descriptions, developer instructions, and safe permissions;
- Codex invocation adapter with schema output, JSONL capture, timeout,
  cancellation, and read-only/workspace-write modes;
- standardized proposal, integration, review, EXECUTE, and OBSERVE prompt contracts;
  and
- fixture/Codex adapter contract tests.

Acceptance:

- Codex discovers all 14 identities from the repository;
- every identity returns its required typed output on a minimal task;
- read-only agents cannot mutate the workspace;
- EXECUTE writes remain inside the generated application workspace; and
- model calls, prompts, results, duration, usage, and raw trace references are
  present in JSON evidence.

Depends on: M0, M1, and M3.

### M5 — React/Vite build profile

Deliver:

- one supported generated-product profile: local React + TypeScript + Vite;
- a rendered standalone HTML Capability Graph covering capabilities, views, journeys, states, and a
  concise visual blueprint;
- verified install, lint, typecheck, test, build, launch, and health-check
  commands;
- isolated application workspace, command allow policy, process timeout, diff,
  dependency inventory, secret scan, and change manifest; and
- Playwright-ready application routes and seeded local data.

Acceptance:

- the fixture app installs, tests, builds, launches, and passes its health check;
- a hanging process is terminated completely;
- a disallowed command or workspace escape is denied and logged;
- the Capability Graph visibly covers the Outcome Model before EXECUTE starts; and
- the EXECUTE gate relies on command and browser evidence, not agent claims.

Depends on: M3 and M4.

### M6 — Chat workflows and UI-first Control Room

Deliver:

- `$train-trust` to start or resume training and report progress in chat;
- `$run-pipeline` to collect one Build Request and start delivery;
- `$pipeline-status` to summarize state and evidence;
- FastAPI endpoints and Server-Sent Events for the delivery run;
- Control Room views for Setup, Live Run, Evidence, and Delivery; and
- visible stages, pair activity, trust-selected lead, gates, repairs, app
  preview, logs, bundle, and video.

Acceptance:

- none of the three workflows requires the user to enter a terminal command;
- training remains chat-only and produces no dedicated training UI;
- a run started in chat appears in the Control Room and survives refresh;
- the UI contains no stage transition or gate decision logic;
- no product decision is requested after the run starts; and
- emergency cancellation reaches a durable terminal state.

Depends on: M2, M3, and M4. UI work may proceed before live training.

### M7 — Automatic demo and Delivery Bundle

Deliver:

- contract-linked demo plan and narration script;
- Playwright capture of the actual generated web app;
- narration provider, captions, ffmpeg composition, and ffprobe validation;
- final manifest, checksums, setup instructions, JSON logs, frozen-vector
  snapshot, application, evidence, and narrated video; and
- Control Room playback and bundle access.

Acceptance:

- a fixture run automatically creates a narrated, decodable video;
- the video contains audio, is under three minutes, and shows the actual app;
- every capture maps to proven Outcome Model items;
- bundle checksums validate after copying; and
- a clean checkout can launch the delivered application from its instructions.

Depends on: M5 and M6.

## 4. Proof sequence

### Phase A — Smoke proof

1. Run the M0 Codex spike before building broad infrastructure.
2. Complete one fixture puzzle round, then all ten fixture rounds.
3. Freeze the fixture vectors and reject a deliberate mutation.
4. Run a fixture Build Request through all seven delivery stages.
5. Build, test, capture, narrate, and export the fixture Delivery Bundle.

Exit gate: the complete control flow works without expensive live training.

### Phase B — Real Trust MoA training

1. Run all seven pairs through the ten approved puzzles using GPT-5.6.
2. Resume from checkpoints rather than restarting completed episodes.
3. Audit puzzle grading, curator updates, call counts, retries, and JSON logs.
4. Freeze the 14 relationship vectors and publish their manifest and digest.
5. Load the published snapshot in a delivery fixture and prove it cannot mutate.

Exit gate: 70 validated team episodes, complete directed-vector evidence, 14
frozen vectors, and one verified digest.

### Phase C — One real autonomous delivery run

1. Choose a small fictional non-banking enterprise and a Build Request that can
   be demonstrated clearly in under three minutes.
2. Invoke `$run-pipeline` in Codex using an inference copy whose trained edges
   inherit the immutable vectors and whose new cross-team edges start at `0.0`.
3. Run SENSE through OBSERVE with one stable run-start relationship snapshot and
   no human decision handoffs. Each receiving team accepts (`1`) or requests a
   revision (`0`); trust deltas remain shadow-only during the run.
4. Build and verify the React/Vite application.
5. Generate and validate the narrated demo automatically, then use one `xhigh`
   Night Curator call to commit reduced-plasticity trust (`α=0.05`, `±0.1`) and
   fast memory before exporting the auditable Delivery Bundle. Never rewrite the
   training snapshot or the nine frozen non-trust dimensions.

Exit gate: every required gate passes and the exported app launches cleanly.

### Phase D — Evidence-led iteration and submission

Fix only problems demonstrated by smoke, training, or live-run evidence. Rerun
the smallest affected scope and preserve before/after run IDs. Then finalize the
public repository, MIT license, setup, frozen-vector evidence, architecture and
trust explainers, hackathon copy, `/feedback` session ID, and public demo video.

The judge route is:

```text
Open the repository in Codex
→ invoke $run-pipeline
→ provide one short Build Request
→ watch a fresh end-to-end run in the Control Room
→ inspect the app, evidence, JSON logs, and generated video
```

Judges use the bundled frozen vectors and do not run `$train-trust`.

## 5. Required proof matrix

| Layer | Required proof |
|---|---|
| Codex configuration | Discovery of `AGENTS.md`, 14 custom agents, project config, and three skills |
| Training | Puzzle audits, 70 episodes, independent directed updates, resume, freeze, digest, mutation rejection |
| Trust resolver | Inbound trust → Dominance → last speaker, including tests for every tie branch |
| Delivery | Full Build Request → Delivery Bundle with fixture and Codex adapters |
| Persistence | Append-only events, stage-boundary resume, tamper detection, immutable versions |
| Build | Workspace isolation, command policy, React/Vite tests/build/launch, browser evidence |
| Repair | One bounded artifact repair, one bounded EXECUTE repair, exhausted-budget failure |
| Control Room | Chat-started run, SSE progress, refresh, evidence, preview, cancellation, completion |
| Media | Playwright capture, narration, captions, composition, duration, decode, checksum |
| Judge route | Frozen vectors preloaded; fresh run starts from Codex chat without training |

## 6. Explicitly deferred

- GitHub Copilot and Claude Code adapters or configuration generators;
- a public ZeroHandoff CLI experience;
- a training dashboard;
- generic software types, additional web frameworks, external databases, auth,
  deployment, and third-party services;
- SQLite projections, rich event replay explorers, and substage recovery;
- unlimited or multi-pass repair graphs;
- terminal/desktop demo capture and multiple narration providers; and
- production multi-tenant isolation for untrusted public users.

## 7. Critical path

```text
M0 → M1
      ├→ M2 → fixture training → real training → freeze 14 vectors
      └→ M3 → M4 → M5 → M6 → M7

frozen vectors + M7
→ one live autonomous delivery run
→ evidence-led fixes
→ hackathon package
→ user review and submission
```

The first blocking proof is the M0 Codex spike. No broad implementation should
continue if agent discovery, GPT-5.6 access, schema output, sandboxed writes, or
JSONL capture cannot be demonstrated in the installed Codex runtime.
