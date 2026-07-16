# ZeroHandoff — Autonomous Software Delivery

**One short build request in. A working, tested application and narrated demo out.**

ZeroHandoff is an autonomous software-delivery pipeline for the OpenAI Build Week
Developer Tools track. A person describes only what they want built, for whom,
the desired outcome, and any constraints. Codex turns that request into a
sequence of deliverables without human handoffs:

`Build Request → Opportunity Model → Outcome Model → Capability Graph → Decision Graph → Scenario Model → Autonomous Change → Evidence + Learning → Demo`

Seven two-agent lifecycle cells—SENSE, MODEL, COMPOSE, DECIDE, SIMULATE,
EXECUTE, and OBSERVE—own those seven stages, respectively. Each pair forms independent
judgments and passes one versioned artifact forward. These compact artifacts are
designed for agent-to-agent execution instead of long-form specifications,
planning ceremonies, or human handoff documents. Codex preserves project memory,
enforces quality gates, and routes failed contract checks through bounded repair
loops.

Every run produces an auditable delivery bundle:

- a runnable application with source and setup instructions;
- automated tests and quality-gate evidence;
- append-only JSON logs covering timestamps, runtime and model configuration,
  the immutable training baseline, run-start inference state, handoff rewards,
  shadow deltas, night commit, decisions, gates, repairs, artifacts,
  and the final outcome; and
- a generated narrated video demo.

Trust training supports the pipeline; it is not the product itself. Before
delivery, a separate ten-round, solver-validated puzzle pilot trains asymmetric
directed trust through reward prediction error and one single-prompt Night
Curator call per agent after each round. The resulting ten-dimensional vectors
form an immutable trained baseline for software-delivery inference: trust alone selects authority, while
a deterministic policy compiler turns up to three strong non-trust dimensions
into qualitative collaboration guidance without exposing raw scores. See the
[visual explainer](context/trust_moa.html) or the
[source specification](context/trust_moa.md) for the full design.

Inference copies that baseline into a separate evolving state. Trained pair
edges begin at their learned values; new cross-team edges begin at `0.0`.
Each next team gives a binary handoff reward—accept `1`, request revision `0`—but
trust stays stable during the run. One extra-high-reasoning Night Curator then
commits reduced-plasticity trust (`α=0.05`, step cap `±0.1`) and consolidated
memory once the prototype completes. The nine non-trust dimensions and the
original training JSON remain unchanged forever.

Codex is the reference runtime for the hackathon. ZeroHandoff’s roles, protocols,
artifacts, gates, and state remain host-neutral so adapters can later support
GitHub Copilot and Claude Code without redesigning the workflow.

Use the three repo skills from Codex chat. Setup, fixture verification, internal
engine commands, and the Control Room are documented in the [local runbook](RUNBOOK.md).

ZeroHandoff is an independent original implementation conceptually inspired by the
[BMad Method](https://github.com/bmad-code-org/BMAD-METHOD). It does not copy or
redistribute BMad source, prompts, agent definitions, names, or branded assets,
and is not affiliated with or endorsed by BMad Code, LLC.

**Explore:** [visual pipeline](overview.html) · [system design](SYSTEM_DESIGN.md) ·
[execution plan](EXECUTION_PLAN.md) · [trust architecture](context/trust_moa.html) ·
[project idea](context/idea.md) · [tracked constraints](HACKATHON_CONSTRAINTS.md)
