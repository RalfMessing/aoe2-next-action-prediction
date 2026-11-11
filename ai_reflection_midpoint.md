# AI-reflection of phase 1

AI-aided pair in phase 1:
- Ralf Messing k12312109
- Fabian Kloibhofer k12306719

Human-Only pair of phase 1
- M Shahab Uddin k12353348
- Abdurahman Alsabagh k12407903

## Part: model, predictions and explorer (Fabian)

Record: `ai-interactions/2026-11-08_phase1_setup-and-model.md`. Decisions and results: `doc/model.md`.

### Setup

One AI with access to the repo folder on my laptop and its own sandbox: Claude (Cowork) implemented the code,
analysed data and results, ran smoke tests and a probe training and drafted the documentation. I defined the tasks
and constraints, ran the pipeline, the official training and the predictions on my machine, reviewed every result,
made the decisions and committed. Result: the trained transformer with checkpoints, `predictions.parquet` with the
plausibility join, a trigram reference on the same target and the build-order explorer.

### Where the AI helped

- **Speed and breadth.** Model, predictions, trigram reference, explorer and a first analysis in one working day.
  Without AI I would have delivered the model and the predictions file, not the explorer and the analyses on top.
- **Justified decisions.** I had every decision checked against the data before adopting it. That turned "collapsing
  is probably right" into numbers (61 % repeats, 60.5 % top-1 for "repeat last action") and surfaced 19 spam sessions
  holding 10 % of all decisions.
- **Thinking ahead to Phase II.** Age not as an input, attention and hidden states exposed, log-spaced early
  checkpoints: the model is built for the analyses we plan next.
- **Checking the shared state.** Opening the committed trigram file showed that it predicts action types, not
  tokens, which the summary did not reveal.

### Where it went wrong

- **Wrong environment.** Install commands for Python 3.12 without checking the installed version (3.14).
- **Confident expectations.** It expected a much deeper prefix tree than the data allows.
- **Tool friction.** Permission issues in a synced folder, two git clients on one checkout.
- **Option creep.** It kept proposing extensions. I stopped after my assigned tasks and handed the ideas to the team.

### What I would do again

- Let the AI read the real files and assignment documents, not summaries.
- Require a check with an explicit invariant before every long run.
- Have the AI justify each decision with the rejected alternatives, and decide myself.
- Recompute every reported number from the actual output files on my machine.
- Compare rates with their baseline noise and label every view by its data basis (held-out vs. in-sample).

### How sure I am

Sure about the headline numbers: reproduced in two independent runs, computed on held-out data, compared with a
baseline on the same target. Less sure about the interpretations (advantage in the opening, anticipation of
age-ups): they are supported by breakdowns and single nodes, but still need the targeted Phase I views. Part of the
implausible predictions are checker false alarms caused by missing age-up commands in the log.

## Part: data pipeline (Ralf)

Record: `ai-interactions/phase1_data_pipeline.md`. Agent prompts and answers: `doc/prompt_phase_*.md`,
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
