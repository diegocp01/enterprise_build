# Devpost submission copy

## Project name

ZeroHandoff

## Tagline

One intent in. Seven trusted agent teams. Working software, proof, and demo out.

## Track

Developer Tools

## Short description

ZeroHandoff is a Codex-native autonomous software-delivery pipeline. A person
provides only a short Build Request; fourteen specialized GPT-5.6 Sol agents,
organized into seven learned two-agent teams, take it through product reasoning,
experience composition, system decisions, execution, verification, and a
narrated end-to-end demo without human handoffs.

## Full description

Traditional agent workflows still ask a human to translate an idea into
specifications, approve handoffs, resolve disagreements, and assemble proof.
ZeroHandoff removes that coordination burden while making autonomy auditable.

Before delivery, seven two-agent teams train on ten split-clue puzzles. Each
partner sees different evidence, so success depends on communication. Binary
outcomes update asymmetric trust through reward prediction error; one
single-prompt Curator call per agent updates the other nine relationship
dimensions and memory after each round. The resulting vectors are hashed and
preserved as an immutable training baseline.

For delivery, one Build Request flows through seven compact, agent-native
artifacts:

**SENSE Opportunity Model → MODEL Outcome Model → COMPOSE Capability Graph →
DECIDE Decision Graph → SIMULATE Scenario Model → EXECUTE Autonomous Change →
OBSERVE Evidence + Learning.**

Each pair forms isolated initial judgments. Learned trust selects authority;
the other relationship dimensions compile into bounded qualitative collaboration
guidance. Deterministic gates validate every version, and failed handoffs route
one bounded repair back to the producer instead of silently advancing.

Learning continues safely between prototypes. The next team accepts a handoff
with reward 1 or requests revision with reward 0. Reduced-plasticity trust
changes accumulate only in shadow, so behavior never oscillates during a run.
After the completed demo, one Night Curator validates lineage, consolidates
evidence-backed memory, commits slow trust once, and leaves the immutable
training JSON and nine non-trust dimensions unchanged.

The canonical run built CoverageCanvas, a local shift-coverage planner, from a
short request. It produced a responsive React application, deterministic tests,
browser acceptance evidence, append-only JSON logs, a verified delivery bundle,
and a narrated 54-second interactive demo. The independent audit scored the run
100/100 while preserving all trust invariants.

## How Codex and GPT-5.6 were used

Codex was both the build environment for ZeroHandoff and its reference runtime.
It helped turn the initial concept into typed contracts, the deterministic
orchestrator, Codex agent configurations, quality gates, repair and resume
logic, browser-driven demo generation, evidence packaging, and regression tests.
At runtime, GPT-5.6 Sol powers all fourteen specialists and the Curators through
schema-constrained calls; Codex supplies isolated workspaces, tool access, and
the chat-native skills that start and inspect runs.

The human made the consequential product decisions: zero human handoffs after
authorization; paired agents instead of copied BMad roles; ten-dimensional
directed relationship vectors; immutable training versus evolving inference;
binary downstream acceptance rewards; reduced trust plasticity; fast memory
versus slow trust; seven agent-native artifacts instead of briefs, PRDs, epics,
and stories; and Codex-only scope for the hackathon.

## What makes it different

ZeroHandoff is not a prompt chain and not a role-play wrapper. It combines
learned asymmetric collaboration, deterministic evidence gates, immutable
artifact lineage, bounded repair, persistent cross-run learning, reproducible
browser proof, and a judge-verifiable final bundle. Its central question is not
only “Can agents build software?” but “Can they learn who to trust, preserve
that learning safely, and prove what happened?”

## Testing instructions

See `JUDGE_GUIDE.md` in the repository. Judges can launch the prebuilt canonical
output with one Python static-server command—no account, backend, install, or
rebuild—or run the full fixture pipeline without model calls. A real GPT-5.6 Sol
run starts from Codex chat with `$run-pipeline`.
