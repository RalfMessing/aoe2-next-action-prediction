# 2026-11-08 · Phase I · Setup, plan and model code

Pair: Fabian Kloibhofer (AI-aided pair, Phase I). Tool: Claude (Cowork, model `claude-opus-5-5`), with access to the
repo folder on Fabian's laptop. Fabian ran everything on Windows himself; Claude wrote code, read data and
checked results in its own sandbox. Prompts are quoted in the original German, followed by a short English gist.

---

## 1 · Getting the repo and deciding how to proceed

**Prompt (Fabian, abridged):**
> „Bitte hole mir unter dem Ordner-UE die aktuelle Version von unserem GitHub. […] Nun ist am Mittwoch die
> Midpoint-Deadline: Daher ist laut meinem Kollegen noch folgendes zu tun (von mir): train the Transformer and save
> checkpoints […] write predictions.parquet (incl. the plausibility join), then start the interactive build-order
> explorer […] Bitte hole das Repo in unseren Ordner und sag mir, wie wir weiter vorgehen sollten."

*Gist:* clone the repo, read the project status, propose how to do Fabian's midpoint tasks.

**AI suggestions**
1. The pipeline lives only on `data-pipeline`; `decisions.parquet` is not committed and must be built locally.
2. **The trigram file on `main` does not match its description.** It predicts the action *type*
   (QUEUE/BUILD/RESEARCH, 3 classes) on uncollapsed data: top-1 74 %, top-5 100 % (trivially, with 3 classes),
   99.6 % at trigram level, while the description says ~35 % / ~64 % and > 80 %.
3. Collapse repetitions, keep the run length as a feature; do not feed the age (Phase II probing).
4. Move the working copy out of OneDrive (`.venv`, a 730 MB database and checkpoints would sync constantly).

**Verification.** Claude loaded the parquet and ran `value_counts` on `true_action` / `pred_top1` and the backoff
levels (point 2). Later (interaction 6) the trigram on the collapsed *token* sequences gave 36.7 % / 67.0 %, close to
the description, so the description probably comes from a token-level run and the uploaded file is a different one.

**Decision.** Plan accepted as the basis. Point 2 goes to the team (human-only pair) instead of being "fixed" by us,
because the pairs are supposed to keep independent pipelines. Point 4 accepted: Fabian connected `Desktop\AI & VIS`
and asked to clone there.

**Effect on the analysis.** We will not join the human-only trigram file into our predictions; we use our own
reference on the same target (see `doc/model.md`, D7).

---

## 2 · To-do list and setup on Windows

**Prompt (Fabian):**
> „Was muss ich machen, damit die Pipeline bei mir läuft und ich mit dem Training starten kann und was muss ich dann
> erledigen! Führe eine Todo-Liste als Artefakt und lass uns diese anschließend Schritt für Schritt durcharbeiten."

**AI suggestions.** Step-by-step list (published as a private page), setup commands for PowerShell, and a warning
that the pipeline scripts open CSV/JSON without `encoding="utf-8"` → set `PYTHONUTF8=1` on Windows.

**Failure (F1).** Claude recommended `py -3.12` without first checking which Python versions were installed.
Fabian's laptop only has 3.14 (`No suitable Python runtime found`). Corrected to 3.14, which the rest of the team
also uses; torch/pyarrow/pandas installed without problems.

**Decision.** Accepted with the correction. Pipeline ran: 11 of 11 checks, 615,296 decisions, 265 tokens,
~4.5 min. Not verified whether the encoding issue would actually have broken the run without `PYTHONUTF8`; the
warning was preventive. Suggestion for the repo owner (not done by us): add `encoding="utf-8"` to the scripts.

---

## 3 · What exactly do we have to deliver?

**Prompt (Fabian, abridged):**
> „Laut meinem Kollegen muss ich nur das machen. Ist das falsch? Wie lange brauchen wir beide […]? UND: Ich bin ja im
> AI-Team. Was muss ich alles dokumentieren?"

**AI suggestion.** Claude read the Moodle assignment text and the lab slides in the course folder instead of
answering from general knowledge. Result: the technical list is right, but the midpoint additionally requires
(a) the AI interaction record of the AI-aided pair, (b) findings with visualizations in the 5-minute video (a
performance table is explicitly not enough), and (c) richer interaction from the AI-aided pair. Estimate: 8-12 h of
Fabian's time over Monday and Tuesday.

**Decision.** Fabian: focus on the two technical tasks; the remaining items are split in the team. Claude keeps
this record and questions/justifies decisions along the way.

---

## 4 · Collapse decision (questioned, not just adopted)

**Prompt (Fabian):**
> „Dokumentiere du alles, was notwendig ist! Ich möchte, dass du selbst Entscheidungen hinterfragst und begründest
> […] Lass uns auf die Teile von Ralf fokussieren."

The task note said "collapsing is probably right". Before adopting it we checked the data (Claude, in the sandbox):

| Check | Result |
|---|---|
| Repeat share after filtering flagged sessions | 61.0 % |
| "Repeat last action" baseline on raw test decisions | 60.5 % top-1 |
| Who repeats | Villager 39 %, Farm 10 %, Knight 9 %, Spearman 7 % |
| Run length | median 1, 90th pct 5, 99th pct 18 |
| Collapsed session length | median 110, 90th pct 197; 40 % longer than 128 |

**Decision.** Collapse, but keep the run length as a bucketed input feature, and keep `first_seq`/`last_seq` per run
for the join back. Rejected: loss re-weighting of repeats, collapsing without run length. Full reasoning in
`doc/model.md` (D1).

**Second decision questioned on the way: dropping flagged sessions.** `outlier_commands` covers only 19 sessions
but 10 % of all decisions (max. 16,528 in one session), so leaving them in would let a few spammers dominate
training. `has_unmapped_events` stays (D2).

---

## 5 · Model, training and prediction code

**What Claude wrote:** `aoe2_sequences.py`, `aoe2_model.py`, `aoe2_trigram.py`, `10_train.py`, `11_predict.py`,
`doc/model.md`. Design choices that came from Claude and were checked against the course plan:
- age not an input (keeps the Phase II probe meaningful),
- hand-written attention so Phase II can read attention maps and hidden states,
- log-spaced early checkpoints for the checkpoint analysis,
- windows that score every target exactly once.

**Verification.**
- Smoke test (60 steps): runs end to end, 890,633 parameters, checkpoints written.
- Invariant: 21,018 scored validation targets = 21,018 runs, every (session, step) exactly once.
- Plausibility join tested on Fabian's laptop against the real `aoe2.db` (1,527 test rows): 0.26 % of the
  *real* actions are flagged implausible, matching the pipeline's calibration (< 0.5 %); 0.1 s runtime.
- Probe training in the sandbox to check the hyperparameters before Fabian's official run (results in the next
  entry once finished).

**Tooling issue (F2).** Claude's first `git clone` into the OneDrive folder failed because the session was not
allowed to delete files (git removes its own lock files). Solved by granting delete permission for the course
folders. Also: Claude's Linux shell and Fabian's Windows git see the same checkout differently (file modes, CRLF),
which shows phantom changes in `reports/`. Claude set `core.fileMode=false` (the Windows default); commits are made
by Fabian on Windows only.

---

## 6 · Official training run and what we read from it

**Prompt (Fabian):** the console output of `10_train.py` (29 epochs, early stop, best val loss 2.124).

**AI analysis.**
- Fabian's run and Claude's probe run match epoch by epoch → reproducible with seed 42 (worth stating in the
  video: the numbers are not a lucky run).
- Validation top-1 plateaus at ~42 % from epoch 12 while the training loss keeps falling: beginning overfitting,
  caught by early stopping. Decision: **no hyperparameter tuning now.** Phase I asks how the model behaves; the
  margin over the trigram (41.9 % vs 36.7 % top-1, 78.9 % vs 67.0 % top-5 on test) is clear enough. Tuning would
  cost the time we need for the analysis.
- Claude broke the test accuracy down by minute, age, action type and confidence (table in `doc/model.md`).
  Largest gain in the opening, gap almost closed at 10-15 min, research predicted worst, slight overconfidence.

**Verification.** Numbers computed from the predictions of Fabian's `best.pt` (copied into Claude's sandbox), not
from the training log alone.

---

## 7 · Build-order explorer, first version

**Origin of the idea.** The explorer's core (choose a prefix, compare model top-5 with players and trigram) came from
the team's task list. Claude added: (a) an icicle view of the whole prefix tree coloured by error rate or by the
transformer-trigram gap (the "prefix tree coloured by error rate" from our project plan), (b) a toggle between
held-out and all sessions, (c) exact means of the full distributions per node.

**Questioned along the way.**
- *Which sessions?* Using all sessions makes the tree deeper (17 vs 15 levels, 437 vs 102 nodes) but the model has
  trained on most of them. Decision: held-out by default, "All sessions" only with an in-sample warning.
- *How deep does it go?* Claude first expected deeper trees. With ≥ 5 sessions per node the exact prefixes end
  after ~15 distinct actions, i.e. openings are shared only that long. We kept exact prefixes (simple, honest) and
  record the depth limit as a finding instead of hiding it with fuzzy matching.

**First insight from the tool.** After *Villager › House › Villager* the transformer predicts Lumber Camp correctly
in 92 % of the sessions, the trigram in 0 % (it predicts Farm, because "House, Villager" happens all game long).

**Status.** Published as a private page for the team and saved in the repo (`explorer/index.html` + `data.js`).
To do: Fabian reviews the design and decides what to change; then we add the plausibility of the model's
suggestions once `predictions.parquet` with the plausibility join is built on his laptop.

---

## 8 · predictions.parquet with the plausibility join

**Prompt (Fabian):** the output of `11_predict.py` on his laptop (21,031 rows, top-1 plausible 98.8 %, true plausible
99.3 %, 3 s).

**AI analysis and how we questioned it.** A raw "98.8 % plausible" sounds good but means little alone, so Claude
compared it with the checker's false-alarm rate on the real actions (0.7 %): the model adds only ~0.5 pp. Then it
looked into the 256 flagged predictions: low confidence (0.30 vs 0.45), mostly age violations right before an
age-up, 31 of them actually the true action. Interpretation kept deliberately cautious: some of the flags are
checker errors caused by missing age commands in the log.

**Verification.** Claude confirmed that Fabian's file has exactly the same predictions as the sandbox run of the
same checkpoint (all 21,031 top-1 tokens equal), then computed the breakdown from his file.

**Effect on the analysis.** The age-up boundary becomes a concrete lead for Phase I ("model anticipates the
age-up"), and the plausibility columns can be used to colour model errors in the explorer and the timelines.

---

## 9 · Explorer extensions: suggested, then handed over

**Prompt (Fabian):**
> „Sind deiner Meinung nach Änderungen im Explorer sinnvoll? Du meintest ich soll meine Wünsche sagen; Ich wüsste
> allerdings nicht, was zum Beispiel"

**AI suggestion.** Four extensions with effort estimates (conspicuous prefixes, "what instead?", age/minute
distribution, adjustable minimum sessions), plus the limitation that the explorer only covers openings, while the
model's weak phase (mid/late game) needs session timelines.

**Decision (Fabian).** Not now: the tasks assigned to Fabian are complete, the extensions go to the team. Claude
documented them as a handover list in `doc/model.md` ("Open ideas for the explorer").

**Reflection.** Fabian had no design wishes of his own here; the ideas came from the AI. Worth discussing in the
reflection: AI assistance widened the options, but whether we pursue them is a time decision of the team, not a
technical one.

---

## 10 · Wrap-up: documentation, Git workflow, message to the group

**Prompt (Fabian):**
> „Sehr gut; Dokumentiere alles ausführlich. Anschließend sagst du mir wie ich es auf Git geben kann und soll.
> Abschließend bereitest du mir noch einen englischen Text für unsere Gruppe vor. […]"

**What Claude did.**
- Checked the documentation for gaps and filled them: draft of `ai_reflection_midpoint.md` for this part, a
  "Model, predictions and explorer" section in `README.md`, the outdated "Current state" in `CLAUDE.md`, the list
  of available checkpoints in `doc/model.md`, and a handover list for the explorer extensions.
- Git workflow (suggestion, executed by Fabian): commit on the feature branch `model` (branched from
  `data-pipeline`), push, open a pull request into `data-pipeline` (or into `main` once Ralf has merged the
  pipeline), keep data, models and generated reports out of the commit.
- Drafted an English status message for the group: what is done (Ralf's two points), what is open (trigram
  alignment, merge, video, reflection with the human-only pair) and optional next steps.

**Decision (Fabian).** Fabian reviews and sends the message himself; commit and push are done by Fabian.

---

## Running lists (feed into `ai_reflection_midpoint.md`)

**Failure patterns**
- F1 · Gives environment-specific commands without checking the environment first (Python version).
- F2 · Tool/environment friction: two git clients on one checkout, permissions in synced folders.
- F3 · A colleague's summary and the committed artifact disagreed; the AI caught it only because it opened the
  file. Summaries (human or AI) are not evidence.
- F4 · Wrong expectation stated with confidence: Claude assumed the exact prefix tree would go much deeper than
  15 levels; only the data showed otherwise.

**Best practices**
- B1 · Verify every AI statement about the data with a query of your own before building on it.
- B2 · Let the AI read the actual assignment documents instead of answering from general knowledge.
- B3 · Smoke tests with explicit invariants (row counts, "each target once") before long runs.
- B4 · Write each decision down with the rejected alternatives; it makes the AI justify instead of just propose.
- B5 · Human runs the official training and commits; AI drafts, checks and documents.
- B6 · Label every view by the data it is computed on (held-out vs. in-sample); an AI-built tool will otherwise
  happily mix them.
- B7 · Ask the AI to break a single score down (by minute, age, action type, confidence) before interpreting it.
- B8 · Compare every rate with its baseline noise (here: plausibility of the model vs. false alarms on real
  actions) before calling it good or bad.
