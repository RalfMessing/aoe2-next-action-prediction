# AI-reflection of phase 1

AI-aided pair in phase 1:
- Ralf Messing k12312109
- Fabian Kloibhofer k12306719

Human-Only pair of phase 1
- M Shahab Uddin k12353348
- Abdurahman Alsabagh k12407903

## Part: model, predictions and explorer (Fabian, with Claude)

Detailed record: `ai-interactions/2026-11-08_phase1_setup-and-model.md`. Decisions and results: `doc/model.md`.
The data pipeline (Ralf) was also built with AI assistance; its prompts and answers are in `doc/prompt_phase_*.md`
and `doc/answer_after_phase_*.md`.

**How we used AI.** Claude (Cowork) worked as a collaborator with access to the repo on Fabian's laptop: it read the
assignment documents and the data, proposed and justified decisions, wrote the model, prediction and explorer code,
ran smoke tests and a probe training in its own sandbox and kept this record. Fabian set the goals, ran the official
pipeline, training and predictions on his machine, made the final calls and commits.

**What it changed.**
- *Speed and breadth.* In one afternoon: data analysis for the collapse decision, model + training, predictions with
  plausibility join, an own trigram reference on the same target, an interactive explorer with a prefix tree, and
  a first breakdown of the results. Without AI we would have had the model and the predictions file, not the
  explorer and the analyses on top.
- *Quality through questioning.* Asking the AI to justify each decision (with rejected alternatives) surfaced
  points we would not have checked: the trigram file on `main` predicts action *types*, not tokens; 19 spam
  sessions hold 10 % of all decisions; feeding the age would make the Phase II probe useless.
- *Verification cost.* Every number was recomputed from files (Fabian's `best.pt`, his `predictions.parquet`);
  running Fabian's training and the AI's probe run side by side confirmed reproducibility. This took maybe a
  quarter of the time but is what makes us confident in the results.

**Failure patterns observed.**
- Commands for the wrong environment (recommended Python 3.12 without checking; only 3.14 was installed).
- Confident but wrong expectations (expected a much deeper prefix tree than the data allows).
- Tool friction: two git clients on one checkout, permission issues in synced folders.
- The AI widens the option space quickly (four explorer extensions); deciding what is worth the time stays with us.

**Emerging best practices.**
- Verify every AI statement about the data with an own query; summaries are not evidence.
- Let the AI read the actual assignment documents instead of answering from general knowledge.
- Smoke tests with explicit invariants before long runs (e.g. every target scored exactly once).
- Write each decision down with the rejected alternatives.
- Break single scores down (minute, age, action type, confidence) and compare rates with their baseline noise.
- Label every view by its data basis (held-out vs. in-sample).
- The human runs the official training and commits; the AI drafts, checks and documents.

**Confidence in our conclusions.** High for the headline numbers (reproduced, computed on held-out data, compared
with a baseline on the same target). Moderate for the interpretations (opening advantage, age-up anticipation):
they are plausible and supported by single nodes and breakdowns, but still need the targeted Phase I views.

*To be completed together with the human-only pair after comparing both processes.*

## Part: data pipeline (Ralf)

Record: `ai-interactions/2026-11-07_phase1_data-pipeline.md`. Agent prompts and answers: `doc/prompt_phase_*.md`,
`doc/answer_after_phase_*.md`.

### Setup

Two AIs with fixed roles. A coding agent in PyCharm implemented and ran the scripts phase by phase. Claude
(claude.ai) reviewed the agent's reports and helped draft the next phase. I set the requirements, chose the data
sources, ran everything, made the decisions and committed. Result: the shared database and training export both
pairs work with.

### Where the AI helped

- **Structure.** From a schema draft and two raw sources to a pipeline with clear phases and stop points. Without
  AI the pipeline would have been a plain export of the commands.
- **Finding problems in the data.** Simulated timestamps, missing age-up commands, replacement buildings logged
  under base names. Most of these would have stayed hidden without the systematic phase reports.
- **Plausibility check.** The idea came from the team; the AI made it testable: a check is only useful if it flags
  almost no real actions and most random ones. The model predictions now use it.

### Where it went wrong

- **Invented project facts.** The AI justified a decision with a preprocessing script that did not exist.
- **Untested output.** A UML diagram with a syntax error, presented as finished.
- **Wrong column in code.** Generic instead of per-civ age for Town Centers; only the report review showed it.
- **Scope creep.** Every review round proposed further refinements. After phase 4 I had the remaining work
  re-evaluated against the project goal: the training data had been complete since phase 3. I stopped the database
  work there.

### What I would do again

- Give the AI the real files and reports, not summaries.
- Let the agent stop after every phase with a report containing numbers that can be checked.
- Check results with domain knowledge, not only with metrics.
- Ask regularly what a step contributes to the goal. The AI does not stop on its own.
- Correct wrong statements about our own project immediately.

### How sure I am

Sure about structure and counts: every phase was validated, and a rebuild on a second machine gave identical
numbers. Less sure about derived fields. Civ inference identifies only 29 % of sessions, and that subset is skewed,
so analyses by civ only hold for it. Some actions the plausibility check flags as impossible are false alarms caused
by missing age-up commands in the log.
