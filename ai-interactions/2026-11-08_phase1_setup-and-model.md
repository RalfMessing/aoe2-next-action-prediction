# Phase I · Model, predictions and explorer

Fabian Kloibhofer, AI-aided pair. Scope: the next-action transformer, `predictions.parquet` with the plausibility
join, the trigram reference on the same target and the build-order explorer (`scripts/aoe2_*.py`, `10`–`12`,
`explorer/`).

**Setup**
- **Claude** (Cowork, `claude-opus-5-5`) with access to the repo folder on my laptop and its own sandbox:
  implemented the code, analysed data and results, ran smoke tests and a probe training, drafted documentation.

I defined the tasks and constraints, set up the environment, ran the pipeline, the official training and the
predictions on my machine, reviewed the code and every result, made the decisions and committed. Decisions and
results are in `doc/model.md`. My prompts are paraphrased below.

---

## 1 · Repository state and plan

**Prompt (paraphrased):** Clone the current repo, review the state of the data pipeline and the trigram baseline,
and propose a plan for my midpoint tasks: train the transformer with checkpoints, write `predictions.parquet` with
the plausibility join, start the build-order explorer.

**AI suggestions.** Work on the `data-pipeline` branch, which holds the pipeline; the export has to be built
locally. Collapse repetitions and keep the run length as a feature; do not feed the age. Keep the working copy
outside OneDrive (virtual environment, 730 MB database and checkpoints would sync constantly).

**Finding.** The `trigram_test_predictions.parquet` on `main` did not match its description: it predicts the action
*type* (QUEUE/BUILD/RESEARCH, 3 classes, 74 % top-1, top-5 trivially 100 %), not the 265 tokens. I had the file
checked directly instead of relying on the summary (F3).

**My decision.** Plan accepted. Working copy moved to a local folder. The trigram mismatch went to the human-only
pair instead of being fixed by us, so the two pipelines stay independent.

---

## 2 · Environment and pipeline run

**Prompt (paraphrased):** Set up a step-by-step task list for the midpoint and give me what I need to run the
pipeline on Windows.

**AI suggestions.** Task list as a shared checklist, PowerShell commands, and a warning that the pipeline scripts
open CSV/JSON without `encoding="utf-8"`, so `PYTHONUTF8=1` should be set on Windows.

**Correction.** The AI's commands assumed Python 3.12 without checking which versions were installed; my laptop runs
3.14, like the rest of the team (F1). I switched the commands to 3.14; all packages installed without problems.

**Result.** Pipeline ran: 11 of 11 checks, 615,296 decisions, 265 tokens, ~4.5 min. Suggestion for the pipeline
(not changed by me): add `encoding="utf-8"` to the scripts.

---

## 3 · Midpoint requirements

**Prompt (paraphrased):** Check whether the task list from the team covers everything the midpoint requires from
the AI-aided pair, and estimate the effort.

**AI answer.** Based on the assignment text and lab slides in the course folder: the technical tasks are complete,
but the midpoint also requires the AI interaction record, findings with visualisations in the video (a performance
table alone is not enough) and richer interaction from the AI-aided pair.

**My decision.** Focus on the two technical tasks; the remaining items are split in the team. Every decision is
questioned and documented with the rejected alternatives as we go.

---

## 4 · Collapse decision

**Prompt (paraphrased):** The team note says collapsing repetitions is "probably right". Check it against the data
before adopting it.

**AI analysis.**

| Check | Result |
|---|---|
| Repeat share after filtering flagged sessions | 61.0 % |
| "Repeat last action" baseline on raw test decisions | 60.5 % top-1 |
| Who repeats | Villager 39 %, Farm 10 %, Knight 9 %, Spearman 7 % |
| Run length | median 1, 90th pct 5, 99th pct 18 |
| Collapsed session length | median 110, 90th pct 197; 40 % longer than 128 |

**My decision.** Collapse, keep the run length as a bucketed input feature and `first_seq`/`last_seq` per run for
the join back. Rejected: down-weighting repeats in the loss, collapsing without run length (D1).

**Filtering.** `outlier_commands` covers only 19 sessions but 10 % of all decisions (up to 16,528 in one session).
I dropped the sessions flagged `short_match`, `no_commands` and `outlier_commands` and kept `has_unmapped_events`,
whose remaining decisions are valid (D2).

---

## 5 · Model, training and prediction code

**Prompt (paraphrased):** Implement data preparation, model, training and prediction as separate modules, with the
constraints from the plan and the Phase II needs in mind.

**Result.** `aoe2_sequences.py`, `aoe2_model.py`, `aoe2_trigram.py`, `10_train.py`, `11_predict.py`, `doc/model.md`.
Design choices I required or accepted, each documented in `doc/model.md`:
- age not an input, so the Phase II probe stays meaningful (D3),
- attention written out by hand, so Phase II can read attention maps and hidden states (D4),
- log-spaced early checkpoints for the checkpoint analysis (D6),
- windows that score every target exactly once (D5).

**Verification.**
- Smoke test (60 steps): runs end to end, 890,633 parameters, checkpoints written.
- Invariant: 21,018 scored validation targets = 21,018 runs, each (session, step) exactly once.
- Plausibility join tested on my laptop against the real `aoe2.db` (1,527 test rows): 0.26 % of the real actions
  flagged, consistent with the pipeline's calibration (< 0.5 %).
- Probe training in the AI's sandbox before my official run, to check the hyperparameters.

**Tooling.** The AI's first clone into the synced course folder failed on file permissions (git removes its own lock
files); the AI's Linux shell and my Windows git also see line endings and file modes differently. Commits are
made on Windows only (F2).

---

## 6 · Official training run

**Prompt (paraphrased):** Evaluate the training output (29 epochs, early stop, best validation loss 2.124).

**AI analysis.**
- My run and the probe run match epoch by epoch → reproducible with seed 42.
- Validation top-1 plateaus at ~42 % from epoch 12 while the training loss keeps falling: beginning overfitting,
  caught by early stopping.
- Test accuracy broken down by minute, age, action type and confidence (table in `doc/model.md`).

**My decision.** No hyperparameter tuning now. Phase I is about behaviour; the margin over the trigram (41.9 % vs.
36.7 % top-1, 78.9 % vs. 67.0 % top-5) is clear. All numbers were computed from my `best.pt`, not from the log.

---

## 7 · Build-order explorer

**Prompt (paraphrased):** Build the explorer from the task list: choose a prefix, compare the model's top 5 with what
players did and what the trigram suggests.

**Result.** Besides the core view: an icicle view of the whole prefix tree coloured by error rate or by the
transformer–trigram gap (the "prefix tree coloured by error rate" from our project plan), exact means of the full
distributions per node, and a toggle between held-out and all sessions.

**Questioned.**
- *Which sessions?* All sessions give a deeper tree (17 vs. 15 levels, 437 vs. 102 nodes), but the model trained on
  most of them. Decision: held-out by default, "All sessions" only with an in-sample warning.
- *How deep?* The AI expected much deeper trees (F4). With ≥ 5 sessions per node, exact prefixes end after ~15
  distinct actions. I kept exact prefixes and report the depth limit as a finding.

**First insight.** After *Villager › House › Villager* the transformer predicts Lumber Camp correctly in 92 % of
the sessions, the trigram in 0 % (it predicts Farm, because "House, Villager" occurs all game long).

---

## 8 · predictions.parquet with the plausibility join

**Prompt (paraphrased):** Interpret the output of `11_predict.py` (21,031 rows, top-1 plausible 98.8 %, true
plausible 99.3 %).

**AI analysis.** 98.8 % alone says little, so it was compared with the checker's false-alarm rate on real actions
(0.7 %): the model adds only ~0.5 pp. The 256 flagged predictions have low confidence (0.30 vs. 0.45), are mostly age
violations right before an age-up, and 31 of them are actually the true action (checker false alarms).

**Verification.** My file contains exactly the same predictions as the sandbox run of the same checkpoint (all
21,031 top-1 tokens equal).

**My decision.** The age-up boundary becomes a concrete lead for Phase I; interpretation kept cautious because the
log misses some age commands.

---

## 9 · Explorer extensions

**Prompt (paraphrased):** Are further changes to the explorer worth it before the midpoint?

**AI answer.** Four extensions with effort estimates (ranking of conspicuous prefixes, "what did the model predict
instead", age/minute distribution per node, adjustable minimum sessions), plus the limitation that the explorer
covers only openings, while the model is weakest in mid and late game.

**My decision.** My assigned tasks are complete; the extensions go to the team as a handover list in
`doc/model.md` ("Open ideas for the explorer"). Mid and late game are better served by session timelines.

---

## 10 · Documentation, Git and team update

**Prompt (paraphrased):** Complete the documentation, describe the Git workflow and draft a status message for the
group.

**Result.** Draft of my part of `ai_reflection_midpoint.md`, model section in `README.md`, updated `CLAUDE.md`,
list of checkpoints in `doc/model.md`. Feature branch `model` from `data-pipeline`, pull request, data and
checkpoints kept out of git. The AI drafted the team message; I set length, tone and content over several
rounds and sent it.

---

## 11 · Final check before the hand-in (2026-11-11)

**Prompt (paraphrased):** Check the repo state after all merges and where the AI records are.

**AI check.** `data-pipeline`, `model` and `Tiagram` are merged into `main`; the human-only pair's trigram script is
in the repo (`scripts/trigram_baseline.py`). Cross-checking `ai_reflection_midpoint.md` against the file list found
a link to a non-existent record file and a leftover placeholder.

**My decision.** Fix the link, remove the placeholder, rewrite my part of the record and the reflection in the
same first-person structure as the pipeline part.

---

## Failure patterns

- **F1 · Commands for the wrong environment.** Install commands for Python 3.12 without checking what was installed.
- **F2 · Tool friction.** Permission issues in a synced folder, two git clients on one checkout.
- **F3 · Summaries are not evidence.** A team summary and the committed trigram file disagreed; only opening the
  file showed it.
- **F4 · Confident but wrong expectations.** The AI expected a much deeper prefix tree than the data allows.
- **F5 · Option creep.** The AI kept proposing extensions; deciding what is worth the time stays with me.

## Best practices

- Verify every statement about the data with an own query before building on it.
- Give the AI the actual assignment documents, not a summary.
- Smoke tests with explicit invariants before long runs (every target scored exactly once).
- Write each decision down with the rejected alternatives.
- Break a single score down (minute, age, action type, confidence) before interpreting it.
- Compare every rate with its baseline noise (model plausibility vs. false alarms on real actions).
- Label every view by its data basis (held-out vs. in-sample).
- Official runs and commits on my machine; the AI drafts, checks and documents.
