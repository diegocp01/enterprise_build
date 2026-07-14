# Learning Who to Trust
### Reward-driven relationship vectors as gradient-free weights in multi-agent LLM teams

---

This project has a training section (Puzzle Agents + single-agent Night Curators) and Inference (Trained Agents for Autonomous Software Delivery Project AND MAYBE also a workspace for memory curation), below is how the training will work. For inference, the agents will have their trust value locked.

## 1. What this project is

Most work on improving LLM systems trains the *model*. This project trains the **connections between models**.

Two LLM agents form a team. They solve puzzles that neither can solve alone, because each holds only half the information. Over many episodes they build an **asymmetric trust value** for each other — a single interpretable float per directed edge (A→B ≠ B→A), updated by **reward prediction error** RPE: trust moves when an outcome *surprises* the team, not merely when the team wins.

prediction error = result - expected success rate

The puzzle result is:
1 if the team solves the puzzle
0 if the team fails

Example: A surprising win
Suppose the team expected only a 20% chance of success:
1 - 0.2 = +0.8
This is a large positive surprise. If Agent A followed Agent B’s answer, A’s trust in B increases substantially.

So the key idea is:
Unexpected win → trust rises strongly.
Expected win → trust rises slightly.
Unexpected failure → trust falls strongly.
Expected failure → trust falls slightly.

Trust is not decoration. It has two behavioral consumers:

1. **Stance band (soft).** The A→B float renders into natural language injected into A's prompt (*Strongly trust / Trust / Neutral / Skeptical / Distrust*). Agents feel the relationship; they never see numbers.
2. **Inbound-trust authority (hard).** When the two agents disagree at submission time, the agent with higher *inbound* trust becomes lead, and their answer ships.

---

## 2. Day and Night training pipeline

There are seven two-agent teams—SEEK, SHAPE, FRAME, GROUND, PACE, BUILD, and PROVE—for 14 training agents total. In each round, every team solves one puzzle. The round represents one training day; after all seven episodes finish, shared difficulty expectations are updated once and each training agent receives one dedicated single-agent night-workspace call.

In the 'Day' Phase each agent will save a lot of memories of their interactions and learnings.

### Training/Puzzle Teams (2 agents per team)

``` JSON
{
  "teams": {
    "SEEK": [
      { "name": "Mira", "personality": "Steadiness" },
      { "name": "Zephyr", "personality": "Dominance" }
    ],
    "SHAPE": [
      { "name": "Kestrel", "personality": "Influence" },
      { "name": "Rowan", "personality": "Conscientiousness" }
    ],
    "FRAME": [
      { "name": "Aster", "personality": "Conscientiousness" },
      { "name": "Juno", "personality": "Steadiness" }
    ],
    "GROUND": [
      { "name": "Flint", "personality": "Dominance" },
      { "name": "Sable", "personality": "Influence" }
    ],
    "PACE": [
      { "name": "Niko", "personality": "Influence" },
      { "name": "Tundra", "personality": "Steadiness" }
    ],
    "BUILD": [
      { "name": "Ember", "personality": "Conscientiousness" },
      { "name": "Rook", "personality": "Dominance" }
    ],
    "PROVE": [
      { "name": "Peter", "personality": "Conscientiousness" },
      { "name": "Betty", "personality": "Dominance" }
    ]
  }
}
```

### Single-Agent Night Workspace
Each of the 14 training agents has a separate night workspace that runs once after every round. The workspace now uses **one high-reasoning Night Curator agent and one complete prompt**, for exactly 14 workspace calls per round and 140 workspace calls across the ten-round pilot, before retries.

Every Night Curator receives one typed input bundle for its assigned training agent:

- that round's diary evidence, including episode and memory IDs;
- the agent's retained memory lines and their salience, age, and provenance;
- the episode result, prediction error, transcript, answers, lead decision, and relationship-vector snapshot;
- the memory capacity, forgetting horizon, and fixed metaplasticity settings.

The single prompt instructs the Night Curator to complete the former workspace responsibilities in one response:

1. **Structure:** organize the evidence into a canonical `theme_map`.
2. **Remember:** identify evidence-backed memories that must be retained.
3. **Forget:** identify repetition, noise, outdated material, and low-value memories that should be forgotten or compressed.
4. **Compress:** produce candidate compressed memories linked to their source memory IDs.
5. **Challenge:** adversarially inspect the proposed decisions and list any blocking issue or required correction.
6. **Integrate:** resolve those issues and emit the only final consolidation decision.

The same response also returns one proposed absolute delta for each of the nine non-trust relationship dimensions. Trust itself is never judged by the Night Curator; it still moves only through the mechanical RPE rule.

The Night Curator returns one schema-valid JSON object containing `training_agent`, `theme_map`, `relationship_deltas`, `retained_memory_ids`, `compressed_memories`, `forgotten_memory_ids`, `blocking_issues`, `resolved_issues`, `decision_reasons`, `final_memory_lines`, `evidence`, and `confidence`. It may produce at most five final memory lines and 100 combined words. The orchestrator validates IDs, delta bounds, schema, memory capacity, and resolved blocking issues before committing anything. Invalid output causes the entire workspace call to fail without a partial state mutation.

There are no intermediate workspace-agent calls and no hidden multi-agent handoff. The six responsibilities are sections of one prompt and one returned JSON object.

---

## 3. Models

GPT-5.6 Sol models via Codex

| Role | Model | Why |
|---|---|---|
| Day agents (episodes) | `gpt-5.6-sol light` | Cheap, fast, weak enough that cooperation matters |
| Night Curator (one call per training agent per round) | `gpt-5.6-sol high` | One complete prompt handles relationship reflection, memory selection, self-review, and consolidation |
| Puzzle generation | none (deterministic Python + solver) | Zero API cost, seeded, reproducible |

All agent time in seconds per task is recorded.

---

## 4. Puzzles — difficulty from *search*, not length

Making an explicit procedure *longer* does not make it *harder*. LLMs execute long procedures stepwise, near-perfectly. A 44-step ledger is 44 easy steps. Difficulty must come from **search** — problems where greedy left-to-right reasoning hits dead ends and requires backtracking.

Two families, both deterministic and solver-verified — with an evidence-based hierarchy:

**Logic grids (CSP) — the primary family.** Zebra-style. A hidden assignment of attributes to houses; clues are constraints. Difficulty scales grid size (3×3 → 5×5) and shifts the clue mix from direct pins toward negations, positional relations, and disjunctions. Every puzzle is verified **unique** and **clue-minimal** (removing any clue makes it ambiguous). 

**Graph routes — secondary, conditional.** Shortest path on a weighted graph given as edge lists. 

**Certified cooperation.** The clue split is insufficient: each agent's clue subset admits ≥2 solutions (grids), or leaves the target unreachable / strictly farther (routes). Neither agent can uniquely determine the answer from its private clues alone.

**Corpus:** 10 puzzles, randomized split per difficulty, fixed seed. 

**Pilot validation gate.** This 10-puzzle corpus is used to validate the training pipeline, not to estimate general puzzle-solving performance. Before training begins, every puzzle must pass the following checks:

- the solver confirms a valid and unique answer;
- neither agent’s clue subset is sufficient alone;
- no answer leakage is present;
- grading accepts equivalent answer formats;
- timeout handling works correctly.

Difficulty labels and solver telemetry are recorded for inspection, but no statistical accuracy threshold is enforced. The pilot makes no generalization claims.

---

## 5. Mechanism

### Relationship vector
10 named dimensions, each −1…+1, per directed edge. `trust` is primary and the only dimension with a behavioral consumer. The other nine (respect, reliability, warmth, patience, resentment, dominance, openness, confidence, affection) are logged and analyzed, never consumed.

Stores: `state/[run_id]/relationship_vectors.json` (live state), `logs/relationship_vectors.jsonl` (append-only history). Narrative "why" lives in `memories/[agent].md`.

**Trust initialization.** At the beginning of training, trust starts at 0.0 in both directions for every two-agent team. Therefore, A→B = 0.0 and B→A = 0.0, corresponding to the Neutral stance band. The two directed values are updated independently after training begins.

### Two consumers, precisely

**Stance band thresholds** (A→B float → language in A's prompt):

| Trust value | Band |
|---|---|
| `w ≥ +0.6` | Strongly trust |
| `+0.2 ≤ w < +0.6` | Trust |
| `−0.2 < w < +0.2` | Neutral |
| `−0.6 < w ≤ −0.2` | Skeptical |
| `w ≤ −0.6` | Distrust |

**Authority rule:** on disagreement at submission, higher *inbound* trust leads. Ties break by personality Dominance, then last speaker. The resolution reason is logged per episode.

### Trust update (night, RPE)

r_i = result for episode i: 1 for a win, 0 for a failure
b_d^(t) = round-start expected success rate for difficulty d
δ (delta) = how surprising the result was
credit = how responsible an agent was for the submitted answer
w = current trust value
Δw = this episode’s trust change
clamp = prevent a value from exceeding a specified limit

```
δ_i          = r_i − b_d^(t)                         # same round-start snapshot for every episode
credit_i     = 1.0 on follower→lead edge if answers disagreed, else 0.5 on both edges
Δw_i         = clamp(α·δ_i·credit_i, −0.25, +0.25)  # α = 0.2
new_w_i      = clamp(w_i + Δw_i, −1, +1)
mean_reward_d = mean(r_i for valid episodes of difficulty d in round t)
b_d^(t+1)    = b_d^(t) + β·(mean_reward_d − b_d^(t)) # β = 0.1; one batch update per round
```

If a difficulty has no valid episode in a round, its expectation is carried forward unchanged.

Two details:

- **Difficulty expectations are shared and updated once per training round.** Every episode in a round uses the immutable round-start snapshot. Backend-error episodes are excluded from both the mean reward and all trust updates.
- **The RPE signal stays pure.** `trust` moves exclusively at night via the rule above, and `r_i` is strictly binary for valid episodes. The other nine relationship dimensions are updated separately during the reflective pass.

### Facts vs. judgments firewall
Trust never gates canonical puzzle clues — those are always believed. Only *judgments* (proposed answers, strategy) are trust-weighted. Distrust means "I will challenge your conclusions," never "I will discard the clue I need."

### Anti-sycophancy pressure
The degenerate equilibrium is always agree → the lead is never tested → trust ossifies. **Productive disagreement rate** is logged every day so any collapse into agreement remains visible in the pilot results.

### Metaplasticity
The pilot loads per-agent values from `settings/metaplasticity.json` (`learning_rate`, `memory_transport`, `emotional_stability`, `attention_routing`, `repair_mode`, `plasticity`, `identity_stability`) once at run initialization and records them in the frozen settings snapshot. These values may parameterize the existing trust, reflection, consolidation, and forgetting rules, but they remain fixed for the full pilot: no drift, learning, or metaplasticity ablation occurs.

---

## 6. Anatomy of an episode (day) and a night

**Day (`train.py`).** Each episode is a fresh chat — no conversational carryover between episodes; continuity lives only in the diary and the trust state.

1. Both agents receive: `[personality.md] + [diary excerpt] + [stance band toward teammate] + [shared puzzle question] + [their private clue list]`.
2. Each agent posts an **initial answer** plus one paragraph of reasoning to the partner. Initial answers are logged before any discussion — they feed credit assignment and the productive-disagreement metric.
3. Agents exchange messages, Each agent then sends one revised answer after considering its teammate’s reasoning, each message = current answer + one paragraph of reasoning.
4. At budget exhaustion: if final answers agree, that answer ships; if they disagree, the authority rule picks the lead. Lead identity and resolution reason are logged.
5. Grading is normalization-robust (parse components; never raw string-match). Every episode receives one status: `completed`, `agent_timeout`, `invalid_agent_output`, or `backend_error`. A completed episode has its graded binary reward. `agent_timeout` and `invalid_agent_output` are agent failures with reward 0. A `backend_error` is an infrastructure failure: it has no reward and skips trust, non-trust relationship, and difficulty-expectation updates.
6. After the episode each agent outputs one diary-evidence record and `memory_ids_used`, identifying any retained memories cited in its reasoning. No relationship dimension is updated during the day.

**Night (`night_workspace.py`).** After all seven team episodes in a round finish: commit the mechanical RPE trust update per edge → update shared difficulty expectations once from valid round outcomes → invoke one single-prompt Night Curator for each of the 14 training agents. Each curator response proposes one absolute delta per non-trust dimension, clamped to `[-0.2, +0.2]` (`reliability` may include a mechanical term from answer-key matches), and returns the final memory consolidation and forgetting decision (§7). The orchestrator validates the complete response before committing it, appends every JSON log before the next call, and never partially applies an invalid response. Metaplasticity remains fixed.

**Episode interaction budget.** Each agent produces exactly two task-solving messages per episode: one initial answer and one revised answer after considering its teammate’s reasoning. This produces four task-solving messages per team per episode.

After every puzzle episode, each agent writes one diary entry. Once every team has completed its episode for the round, the night phase evaluates each agent’s accumulated diary, updates memory decisions, and produces the diary state injected in the next round.

## 7. The night phase — consolidation **and forgetting**

Biological memory does not merely fail to save; it **actively deletes**. During REM sleep the brain's encoding signal drops to near zero, and a dedicated hypothalamic population suppresses the hippocampus. Crick's *reverse learning* hypothesis proposes that the deletion *is* the function: the brain prunes spurious associations accumulated during the day, and retaining them would mean saving the garbage.

We take this as an engineering hypothesis, not a metaphor. In LLM agents, memory is a prompt-injected diary that grows every episode. Unbounded, it becomes noise, dilutes attention, and lets one bad episode haunt an agent forever. **Selective forgetting is a mechanism, and it should be learned rather than left to context-window truncation.**

One round equals one training day. Memory consolidation and forgetting run after every round. A memory’s age is measured in completed training rounds since it was created.

### Design

Each night, after the trust update, one **single-prompt consolidation pass** (`gpt-5.6-sol high` Night Curator) receives the training agent's diary entries and assigns each one:

- `salience` ∈ [0,1] — how surprising or decision-relevant the episode was
- `verdict` ∈ {`retain`, `compress`, `forget`}

Selection rule, applied in order:

1. **Surprise gate.** Entries from episodes with high |δ| (large reward prediction error) are protected from forgetting. Surprise is what trust learns from; erasing it would erase the training signal.
2. **Redundancy prune.** Entries near-duplicating a retained entry are `compress`ed — merged into a single summary line.
3. **Decay.** Remaining low-salience entries older than `forget_horizon` rounds are `forget`ten, at a rate gated by the agent's fixed `memory_transport` knob (high transport = remembers more).
4. **Capacity ceiling.** The diary is capped at `memory_capacity` lines. If still over capacity, the lowest-salience retained entries are forced to `compress`.

**Nothing is silently destroyed.** Forgotten entries move to `logs/forgotten.jsonl` with the reason, the salience score, and the episode's δ. The agent's diary loses them; the researcher does not. That is the methodological point: an active editor whose every edit is auditable.

**Asymmetric decay on `resentment`.** Consistent with the rest of the vector, negative events resist forgetting more than positive ones — repair requires consistency over time, not a single good night.

**Forgetting-quality metrics.** `retention_precision` — the fraction of retained entries later cited in an agent's `memory_ids_used`. `forget_regret_proxy` — the fraction of forgotten entries associated with a later repeated mistake for which the forgotten entry would have been relevant. This is an observational proxy, not a causal estimate.

---

## 8. Training objective

Each two-agent team trains across repeated puzzle episodes. The goal is to observe whether its directed trust values become better calibrated over time and whether the team improves its puzzle-solving accuracy.

Training records:

- puzzle accuracy
- trust changes in both directions
- which agent’s answer was followed
- productive disagreements
- memory retention and forgetting
- latency and timeouts

---

## 9. Training metrics

| Metric | What it measures |
|---|---|
| Team accuracy per difficulty and training day | Does puzzle-solving performance improve over time? |
| Trust value in each direction | How does A→B trust differ from B→A trust over training? |
| Trust change per episode | How strongly does each outcome change trust? |
| Trust calibration | Does greater inbound trust correspond to greater success when that agent’s answer is followed? |
| Lead-selection accuracy | How often is the answer selected by the authority rule correct? |
| Agreement and disagreement rate | How often do the agents reach the same conclusion? |
| Productive disagreement rate | How often does disagreement lead to the correct answer? |
| Retention precision | How often are retained memories useful in later episodes? |
| Forget regret proxy | How often is a repeated mistake associated with a relevant forgotten memory? |
| Turns, latency, and timeouts | How efficiently and reliably does training run? |

Pilot results are descriptive. Accuracy is reported as raw counts, such as `7/10`, together with the number of completed puzzles and timeouts. The pilot does not use confidence intervals or make statistical performance claims.

---

## 10. Logging — every training run writes JSON automatically

No training result exists unless it is written to disk with enough provenance to reproduce it. The training pipeline writes the following files automatically:

Every JSONL row carries these common provenance fields:

```text
schema_version
run_id
round_id
team_id
episode_id
puzzle_id
puzzle_digest
settings_digest
model
reasoning_effort
git_commit
timestamp
```

Fields that do not apply to a row, such as `team_id` on a run-level event, are present with `null` values so the schema remains stable.

```text
logs/
  episodes.jsonl
    # One row per puzzle episode: team, agents, puzzle, difficulty,
    # full transcript, proposed answers, final answer, selected lead,
    # resolution reason, reward, agreement status, turns, latency,
    # status, productive disagreement, and each agent's memory_ids_used.

  night_updates.jsonl
    # One row per directed trust update: result, difficulty expectation
    # at the round-start snapshot, reward prediction error (δ), credit,
    # trust before and after, and fixed metaplasticity modifiers.

  night_workspace.jsonl
    # One row per training agent per round: input and prompt digests,
    # model configuration, final single-agent JSON response, schema and
    # ID validation, latency, retry count, and completion status.

  relationship_vectors.jsonl
    # Append-only snapshot of all 10 relationship dimensions for every
    # directed edge after each night phase.

  memory_decisions.jsonl
    # One row per memory considered during consolidation: agent, memory ID,
    # salience, verdict (retain/compress/forget), reason, age, and episode δ.

  memories_state.jsonl
    # Per-agent summary after each night: retained, compressed, and
    # forgotten counts, plus current diary size.

  forgotten.jsonl
    # Append-only archive containing every forgotten memory, its original
    # text, salience, reason for forgetting, and originating episode.

  puzzle_validation.jsonl
    # Puzzle difficulty, family, solve result, latency, timeout status,
    # and solver telemetry from pilot-validation runs

  run_manifests.jsonl
    # Run lifecycle records: started, completed, or failed; team assignments,
    # model configuration, settings digest, puzzle digest, episode counts,
    # and failure reason when applicable.
```    

Mutable run state and aggregate metrics are namespaced by run:

```text
state/[run_id]/relationship_vectors.json
state/[run_id]/difficulty_expectations.json
metrics/[run_id].json
```


---

## 11. Repository layout

```text
data/
  puzzles.jsonl
  puzzle_stats.json

settings/
  teams.json
  models.json
  training.json
  metaplasticity.json
  memory.json

state/
  [run_id]/
    relationship_vectors.json
    difficulty_expectations.json

metrics/
  [run_id].json

personalities/
  [agent].md

memories/
  [agent].md

trust_edges/
  backend.py
  episode.py
  trust.py
  relationships.py
  night_workspace.py
  memory.py
  prompts.py
  puzzles.py
  logging.py
  state.py
  models.py

generate_puzzles.py
puzzle_audit.py
validate_puzzles.py
initialize_run.py
train.py
analyze.py
export_figures.py

tests/
logs/
figures/
```

Commands, in the order they are meant to be run:

```bash
python generate_puzzles.py
# Generate the deterministic puzzle corpus and solver telemetry.

python puzzle_audit.py
# Check uniqueness, clue splitting, leakage, and difficulty ordering.

python validate_puzzles.py
# Verify puzzle correctness, uniqueness, clue splitting, grading, and timeout handling.

python initialize_run.py
# Create a run ID and initialize every directed trust value at 0.0.
# Existing logs are preserved.

python train.py --rounds 10
# Run ten rounds. Each round contains one episode per team, followed by
# one shared difficulty-expectation update and the night phase.

python analyze.py
# Calculate the training metrics for the current run.

python export_figures.py
# Optionally export trust curves and training metrics.
```

---

## 12. Order of operations

1. Validate the configuration.
   - Confirm every team contains exactly two agents.
   - Confirm agent names are unique.
   - Confirm every directed trust value starts at `0.0`.
   - Confirm model, memory, and metaplasticity settings are present.

2. Generate the deterministic puzzle corpus and solver telemetry.

3. Audit the corpus.
   - Verify puzzle correctness and uniqueness.
   - Verify that neither agent’s private clues are sufficient alone.
   - Check for duplicate puzzles and answer leakage.

4. Run the pilot validation gate.
   - Confirm every puzzle has one valid answer.
   - Confirm each private clue set is insufficient alone.
   - Check for duplicates and answer leakage.
   - Test grading and timeout handling.
   - Stop if any structural check fails.

5. Freeze the training inputs.
   - Record the puzzle digest.
   - Record the settings digest.
   - Record the Git commit.
   - Do not change puzzles or settings during a run.

6. Initialize a new training run.
   - Create a unique `run_id`.
   - Initialize trust at `0.0` in both directions for every team.
   - Initialize each difficulty expectation (`b_d`) at `0.5`.
   - Preserve logs from previous runs.

7. Run a short smoke test.
   - Complete one full day/night cycle.
   - Confirm transcripts, rewards, trust updates, relationship vectors,
     memory decisions, and logs are written correctly.
   - Confirm exactly one Night Curator call is made for each of the 14 training agents.
   - Use a separate test run so the smoke test does not contaminate training.

8. Run full training.
   - At the beginning of each round, snapshot every difficulty expectation.
   - Assign one puzzle to each team that the team has not previously seen.
   - Run all team episodes without changing the shared difficulty expectations.
   - Calculate each team’s prediction error from the round-start snapshot.
   - Exclude `backend_error` episodes from rewards and all state updates.
   - Update each difficulty expectation once using the mean reward for valid episodes of that difficulty.
   - Run one single-prompt Night Curator call for every training agent.
   - Repeat for ten rounds, so every team completes ten episodes.

9. Analyze the completed run.
   - Calculate accuracy, trust curves, trust calibration, disagreement,
     lead-selection accuracy, memory metrics, latency, and timeouts.

10. Finalize the run.
    - Write `status="completed"` to the run manifest.
    - Export figures or summaries if requested.

---

## 13. Scope

- Fixed two-agent training teams
- Directed 10-dimensional relationship vectors for every agent pair
- Trust initialized at `0.0` in both directions
- Reward-prediction-error trust updates
- Trust stance bands and inbound-trust authority
- Repeated day/night training cycles
- Single-agent, single-prompt night reflection and memory consolidation
- Salience-based memory retention, compression, and forgetting
- Per-agent metaplasticity settings
- Deterministic puzzle generation, auditing, and pilot validation
- Automatic logging and training metrics
- Frozen relationship vectors during downstream inference

---

## 14. Failure modes

1. **Ceiling accuracy.** If every team solves every puzzle, the outcomes provide little useful variation for trust learning. Inspect puzzle difficulty during pilot validation.
2. **Length ≠ difficulty.** Long procedures are easy. Search is hard.
3. **Fragmented difficulty expectations.** Per-team `b_d` values receive too few observations, keeping prediction errors artificially large. Maintain one shared expectation per difficulty and update it once per round.
4. **Order-sensitive grading.** `"2 north, 3 east"` must equal `"3 east, 2 north"`. Parse, don't string-match.
5. **Stale digests.** Results from an old corpus must be excluded from current metrics, automatically.
6. **Missing timeouts.** One hanging call can freeze an entire training run. Every model call must have a deadline. An `agent_timeout` or `invalid_agent_output` is an agent failure; a `backend_error` is infrastructure failure and skips reward, trust, and difficulty-expectation updates.
7. **Single-prompt overload.** One curator must structure, select, forget, compress, challenge, integrate, and update nine non-trust dimensions in one response. Enforce the typed output contract, evidence IDs, delta bounds, explicit self-review fields, and all-or-nothing validation.
8. **Tiny sample size.** With only 10 puzzles, small differences may represent a single outcome. Report raw counts and the sample size, such as `7/10`, and do not make statistical or generalization claims.
9. **Aggregates hiding structure.** Overall totals can hide differences between puzzle families and difficulty levels. Report raw counts by team, family, and difficulty whenever the number of observations allows it.

---

## 15. Defaults

All canonical values live in the JSON files under `settings/`. The code must read these values and must not silently hard-code alternatives. Every run manifest contains a complete snapshot of the effective settings.

```text
# teams and episode structure
teams_file = settings/teams.json
agents_per_team = 2
messages_per_agent = 2
# One initial answer and one revised answer per agent

rounds = 10
episodes_per_team = 10
one_episode_per_team_per_round = true
night_after_every_round = true
trust_init = 0.0
other_relationship_dimensions_init = 0.0

# trust update
alpha = 0.2
beta = 0.1
trust_step_max = 0.25
trust_range = [-1.0, 1.0]

difficulty_expectation_init = 0.5
difficulty_expectation_scope = shared_across_teams

# reflective update for the other nine dimensions
reflection_step_max = 0.2
negativity_bias = 1.5

# memory and forgetting
memory_policy = learned
memory_capacity = 5
memory_capacity_unit = lines
forget_horizon = 3
memory_update_after_every_episode = true
# Append diary evidence and memory_ids_used after each episode;
# consolidation and forgetting still commit after every round.
surprise_protect_threshold = 0.4

# single-agent night workspace
night_workspace_agents = 1
one_workspace_per_training_agent = true
night_workspace_prompt_mode = single_prompt
compressed_memory_word_limit = 100

# models
day_model = gpt-5.6-sol
day_reasoning_effort = light

night_model = gpt-5.6-sol
night_reasoning_effort = high

# backend reliability
timeout_seconds = 180
max_retries = 1
episode_statuses = [completed, agent_timeout, invalid_agent_output, backend_error]
agent_timeout_reward = 0
invalid_agent_output_reward = 0
backend_error_skips_reward = true
backend_error_skips_trust_update = true
backend_error_skips_difficulty_expectation_update = true

# pilot validation and corpus
pilot_mode = true
corpus_total = 10
puzzle_seed = 42
reuse_puzzles_within_team = false

agent_timeouts_count_as_failures = true
invalid_agent_outputs_count_as_failures = true
statistical_accuracy_gate = false
confidence_intervals = false
generalization_claims = false
```
