# ZeroHandoff — Autonomous Software Delivery

**One short build request in. A working, tested application and narrated demo out.**

ZeroHandoff is an autonomous software-delivery pipeline for the OpenAI Build Week
Developer Tools track. A person describes only what they want built, for whom,
the desired outcome, and any constraints. Codex turns that request into a
sequence of deliverables without human handoffs:

`Build Request → Product Brief → PRD → UI/UX Screens → Architecture Diagram → Epics & Stories → Implementation → PRD Verification → Demo`

Seven two-agent delivery cells: SEEK, SHAPE, FRAME, GROUND, PACE, BUILD, and
PROVE—own those seven delivery stages, respectively. Each pair forms independent
judgments and passes one versioned artifact forward. Codex preserves project
memory, enforces quality gates, and routes PRD failures through bounded repair
loops.

Every run produces an auditable delivery bundle:

- a runnable application with source and setup instructions;
- automated tests and quality-gate evidence;
- append-only JSON logs covering timestamps, runtime and model configuration,
  the frozen relationship-vector snapshot, decisions, gates, repairs, artifacts,
  and the final outcome; and
- a generated narrated video demo.

Trust training supports the pipeline; it is not the product itself. Before
delivery, a separate ten-round puzzle pilot trains asymmetric directed trust
through reward prediction error and one single-prompt Night Curator call per
agent after each round. The resulting relationship vectors are frozen during
software-delivery inference. See the
[visual explainer](context/trust_moa.html) or the
[source specification](context/trust_moa.md) for the full design.

Codex is the reference runtime for the hackathon. ZeroHandoff’s roles, protocols,
artifacts, gates, and state remain host-neutral so adapters can later support
GitHub Copilot and Claude Code without redesigning the workflow.

ZeroHandoff is an independent original implementation conceptually inspired by the
[BMad Method](https://github.com/bmad-code-org/BMAD-METHOD). It does not copy or
redistribute BMad source, prompts, agent definitions, names, or branded assets,
and is not affiliated with or endorsed by BMad Code, LLC.

**Explore:** [visual pipeline](overview.html) · [project idea](context/idea.md) ·
[trust architecture](context/trust_moa.html) · [hackathon overview](context/overview.md) ·
[rules](context/rules.md) · [tracked constraints](HACKATHON_CONSTRAINTS.md)
