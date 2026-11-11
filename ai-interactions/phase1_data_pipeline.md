# Phase I · Data pipeline

Ralf Messing, AI-aided pair. Scope: the shared database and training export (`scripts/00`–`08`).

**Setup**
- **Claude** (claude.ai chat, `claude-opus-5-5`): reviewer and sparring partner for design decisions, phase
  reports and prompts for the agent.
- **Coding agent in PyCharm** (Claude Code using Opus 5.5): implemented and ran the scripts, one phase at a time.

I defined the requirements, chose the data sources, ran every phase, reviewed the reports, made the decisions and
committed. The full prompts to the agent and its answers are in `doc/prompt_phase_*.md` and
`doc/answer_after_phase_*.md`. My prompts to Claude are paraphrased below.

---

## 1 · Schema review

**Prompt (paraphrased):** Review a team member's proposed database schema (UML) for structural problems and check
whether the planned extras are feasible with this data: civ inference, resource history, plausibility of model
predictions.

**AI suggestions.** Link events to game objects, add timestamps to the resource history, model prerequisites as
n:m, use a join table for players per match, store civ labels in their own tables. Resource history only as a
lower bound, because the log records spending but not income.

**My decision.** Revised schema accepted as the basis. Prerequisites became the main plausibility check, the
resource estimate a secondary one. The revised PlantUML did not render; I sent the error back and got a corrected
version (F1).

---

## 2 · Data sources

**Prompt (paraphrased):** Compare three candidate AoE2 APIs for the static game data (costs, times, tech trees
per civ). Also: which event types beyond the obvious ones are worth persisting? And why the Zenodo SQLite rather
than the JSON export of the same log?

**AI suggestions.** All three links point to the same outdated API (base game only, no longer hosted). Use
aoe2techtree `data.json` for the rules. Zenodo SQLite for the replays, because it can be queried without loading
1.6 GB into memory and is already split into OCEL tables.

**My decision.** aoe2techtree for rules, Zenodo SQLite for replays. Before deciding, I clarified with the team
member who proposed the schema that "not Zenodo" referred only to the static tables.

---

## 3 · Specification for the coding agent

**Prompt (paraphrased):** Raw data (Zenodo SQLite, `data.json`) is in place. Turn the agreed design into a
phase-by-phase specification for the coding agent.

Before that, I corrected a claim in Claude's earlier answer: it had partly justified SQLite with an existing
preprocessing script in our repo. No such script exists (F2).

**Result.** A specification in six phases (exploration → schema + static data → replays → civ inference →
plausibility → validation + export). Rules: never modify source files, don't invent data, flag every estimate,
idempotent scripts, stop and report after each phase.

**My part.** Set up the project and environment, adapted the specification, ran the agent.

---

## 4 · Phases 1–3: exploration, static data, replays

Workflow for every phase: the agent implements and reports, I review the report together with Claude, decide and
release the next phase.

**Phase 1 · Exploration.** Key finding: only Command timestamps are observed; Start/Complete times are simulated by
the dataset authors. Decision: training sequences use Commands (plus Start Build for buildings).

**Phase 1.5 · Matching the rules version.** The log did not fully match the current tech tree. I searched the
aoe2techtree history for the version closest to the replay period and found the last commit of 2023 (15 Dec 2023,
one day after the last replay). I had the agent compare the 2023 and 2026 versions against the log. 2023 fits
clearly better, so it became the primary source, with 2026 as fallback.

**Phase 2 · Schema and static data.** I had Claude compare the generated schema with the agreed UML. Found: Town
Center age taken from the generic column instead of per civ (F4), missing pop cost, and `03` resetting the whole
database. Fixes: per-civ age, `run_all.py` for the correct run order, a "known limitations" section in the README.

**Phase 3 · Replays.** Event counts match the source for every type. I checked a sample session against game
knowledge (Meso start, Feudal Age at 6:44, archer opening). The database (~700 MB) stays out of git.

---

## 5 · Phases 4–5: civ inference, plausibility, scope

**Phase 4 · Civ inference.** Three civs were never identified. Hypothesis: the log uses base names for replacement
buildings (e.g. Folwark logged as "Mill"). Confirmed by the agent in Phase 4.1, fixed with an equivalence table.

**Scope check.** After Phase 4 each review round was adding new refinements.

**Prompt (paraphrased):** Re-evaluate the remaining phases against the actual project goal: what does the model
need, what does the Phase I analysis need, and what is optional?

**AI answer.** The training data has been complete since Phase 3. Civ inference and resource history are extras.
The plausibility check is the only extra that yields a Phase I finding. Claude also noted that it had widened the
scope itself with its suggestions (F3).

**My decision.**
- No further work on civ inference; resource estimate kept minimal.
- Finish and validate the plausibility check: it must flag almost no real player actions but most random ones.
- Then only validation, README and export.

---

## 6 · Export and handover

**Phase 6 + export.** `decisions.parquet` (615,296 decisions, 265 tokens), `vocab.csv`, `split.csv` (80/10/10 by
match, fixed seed), `reports/validation.md` (11 of 11 checks). Repetitions are not collapsed and flags are columns,
not filters: both are modelling decisions and belong to the model step.

**Prompt (paraphrased):** Summarise the pipeline for the team (what it delivers, its limits, how to run it)

**AI answer.** updated the documentation

---

## Failure patterns

- **F1 · Untested output.** A diagram handed over as finished that did not render.
- **F2 · Invented project facts.** The AI argued with a script that did not exist. Only knowing the repo caught it.
- **F3 · Scope creep.** Each review suggested one more improvement. Left unchecked, the database work would have
  delayed the model.
- **F4 · Plausible but wrong code.** Generic instead of per-civ column; only visible in the phase report.

## Best practices

- Separate roles: the agent implements, a second AI reviews, the human decides.
- Every phase ends with "stop and report".
- Reports contain checks that must hold (counts source vs. target, false-alarm rate on real actions).
- Validate results with domain knowledge, not only with numbers.
- Manual decisions live in small CSVs (`manual_mapping.csv`, `civ_group_labels.csv`) so reruns never overwrite them.
- Re-check the remaining work against the project goal regularly; the AI does not do it on its own.