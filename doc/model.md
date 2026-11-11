# Next-action model: decisions and how to run it

Phase I, AI-aided pair (Fabian Kloibhofer). This file records *what* was built and *why*,
including the rejected alternatives. The AI interaction record is in `ai-interactions/`.

## Run it

Prerequisite: `scripts/run_all.py` has produced `data/processed/` (see README "Setup").

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\10_train.py                 # ~15-20 min on a laptop CPU
.venv\Scripts\python scripts\11_predict.py               # writes data/processed/predictions.parquet
.venv\Scripts\python scripts\12_explorer_data.py         # writes explorer/data.js; open explorer/index.html
```

| Script / module | Purpose |
|---|---|
| `scripts/aoe2_sequences.py` | Filter sessions, collapse repetitions, build model inputs and windows |
| `scripts/aoe2_model.py` | The causal transformer (returns attention and hidden states for Phase II) |
| `scripts/aoe2_trigram.py` | Trigram reference with backoff on the same target |
| `scripts/10_train.py` | Training, checkpoints, `metrics.csv` |
| `scripts/11_predict.py` | `predictions.parquet` with model, trigram and plausibility columns |
| `scripts/12_explorer_data.py` | Prefix tree data for the explorer (`explorer/data.js`) |
| `explorer/index.html` | Build-order explorer, opens directly in the browser (no server needed) |

Outputs: `models/<run-name>/` (checkpoints, `config.json`, `metrics.csv`; not committed) and
`data/processed/predictions.parquet` (not committed, shared via the team folder).

## Decisions

### D1 · Collapse consecutive repetitions: yes, keep the run length as a feature

*Question.* 63.9 % of all decisions repeat the previous token (61.0 % after filtering), mostly Villager (39 % of the
repeats), Farm, Knight and Spearman. Should the model see and predict them?

*Decision.* One input position per *run* of identical tokens; the model predicts the next **different** action.
The run length goes in as a bucketed feature (1, 2, 3-4, 5-8, 9-16, 17+).

*Why.*
- Without collapsing, "repeat the last action" alone is right in 60.5 % of the raw test decisions. Accuracy would
  mostly measure queue spam and hide exactly the decisions we want to analyse (what comes after the opening, after
  an age-up).
- The build-order explorer and the prefix tree are about the order of different actions; repeats would make every
  prefix start with long Villager chains.
- Sequences get 2.8x shorter (median 110 runs per session), so the 128-token context covers the whole session for
  60 % of the sessions instead of a few minutes of game time.

*What we give up and how we limit it.* The *number* of repetitions is no longer a prediction target. It stays
visible to the model through the run-length feature, and every run keeps `first_seq`/`last_seq`, so results can be
joined back to `decisions.parquet`.

*Rejected.* (a) Keep repeats and weight them down in the loss: still dominates the metrics and the explorer.
(b) Collapse without run length: loses information such as "5 Spearmen in a row" vs. one.

### D2 · Drop sessions flagged `short_match`, `no_commands`, `outlier_commands`; keep `has_unmapped_events`

103 of 1,969 sessions (63,065 decisions). `outlier_commands` are only 19 sessions but 10 % of all decisions
(up to 16,528 per session), i.e. spam that would dominate training. `has_unmapped_events` is kept because the UNKNOWN
events are already outside the vocabulary; the other decisions of these sessions are valid.

### D3 · Inputs: token + run start minute + run-length bucket + position. Not the age.

- Game time matters (the same prefix means something else at minute 3 and minute 20). We use the minute of the
  run's first decision, clipped at 90 (99 % of decisions are before minute 59).
- The time of the *target* is not an input, it would leak information about what comes next.
- **The current age is deliberately not an input.** Phase II probes the hidden states for the current age; if the
  age were an input, the probe would trivially succeed and tell us nothing about what the model learned.

### D4 · Architecture: 4 layers, d = 128, 4 heads, FF 512, context 128 → 890,633 parameters

Within the planned 0.5-1 M. Pre-norm blocks, learned position embeddings, dropout 0.1, separate output head over
the 265 actions (no output for PAD/BOS). The attention is written out by hand instead of `nn.TransformerEncoder`
because Phase II needs attention weights and per-layer hidden states (`return_internals=True`).

### D5 · Windows: stride 64, every target scored exactly once

Sessions longer than 128 positions are cut into overlapping windows. The first window scores all positions, every
later window only its last 64, so each target is counted once and sees at least 64 positions of history. Verified:
21,018 scored targets in validation = 21,018 runs.

### D6 · Training and checkpoints

AdamW (lr 1e-3, weight decay 0.1 on matrices only), 200 warm-up steps, cosine decay to 10 %, gradient clipping 1.0,
batch 64, up to 40 epochs, early stopping after 5 epochs without validation improvement, seed 42.
Checkpoints at step 0, at log-spaced steps 16, 32, 64, ... (the early phase changes fastest, which matters for the
Phase II checkpoint analysis), after every epoch, and `best.pt` (lowest validation loss).

### D7 · Our own trigram reference on the same target

The trigram file of the human-only pair (`trigram_test_predictions.parquet` on `main`) predicts the action *type*
(QUEUE/BUILD/RESEARCH) on uncollapsed data, so it cannot be compared with the transformer position by position.
`aoe2_trigram.py` fits a trigram with backoff on the collapsed token sequences of the training split. It is our
reference for the explorer and the "where does longer context help" comparison; the human-only pair keeps its own
baseline (the assignment wants the two pipelines to stay independent). First result on test: top-1 36.7 %,
top-5 67.0 %, 98.7 % of predictions at trigram level.

### D8 · Plausibility join: checked at the moment the player actually acted

For each test step, `11_predict.py` asks `aoe2_plausibility.Checker` whether the true action and each of the model's
top-5 could have been done at `start_time_s` of the true run (evidence strictly before that time). Columns:
`true_plausible`, `top1_plausible`, `top1_reasons`, `top5_n_plausible`. `true_plausible` gives the false-alarm rate
of the checker on real actions (0.26 % on a 1,527-row probe), which is the yardstick for reading `top1_plausible`.

### D9 · Build-order explorer: exact prefixes, held-out by default

`scripts/12_explorer_data.py` builds a prefix tree of collapsed actions and `explorer/index.html` shows, for the chosen
prefix, what players did next, the transformer's mean distribution and the trigram's top-5, plus an icicle view
of the whole tree coloured by error rate (or by the transformer-trigram gap).

- *Exact prefixes, not fuzzy matching.* Simple to explain and reproduce. Consequence: with ≥ 5 sessions per node the
  tree ends after 15 (held-out) or 17 (all sessions) distinct actions. That is a finding in itself: openings are
  shared for roughly the first 15 decisions, then players diverge.
- *Held-out by default.* The default view uses only val + test sessions (371) so the accuracies are honest. The
  "All sessions" view (1,866, deeper tree) is marked as in-sample.
- *Means of full distributions.* The model also sees minute and run lengths, so sessions with the same prefix get
  different predictions; the node shows the mean of the full softmax (not of top-5 lists, which would be biased).

## Results of the first run (`transformer_v1`)

Training (laptop CPU): 29 epochs in 12 min, early stop; best validation loss 2.124 at epoch 24
(step 888). A second, independent run gave identical numbers for every epoch, so the run is reproducible
with seed 42.

Checkpoints in `models/transformer_v1/` (for Phase II): `step_0000000.pt` (untrained) and log-spaced `step_0000016.pt` … `step_0001024.pt`, `epoch_001.pt` … `epoch_029.pt`, `best.pt` (= epoch 24, step 888), plus `config.json` and `metrics.csv`. Each checkpoint stores model and training configuration and the SHA-256 of `vocab.csv`; `11_predict.py` refuses a checkpoint whose vocabulary does not match.

*Questioned:* validation top-1 plateaus at ~42 % from epoch 12 while the training loss keeps falling, i.e. the model
starts to overfit, and early stopping catches it. We did not tune further on purpose: Phase I is about *behaviour*,
and the margin over the trigram is already clear. Ablations (no run length, no minute) are candidates if Phase II
needs them.

Test split (21,031 steps, 190 sessions):

| | Top-1 | Top-5 |
|---|---|---|
| Transformer | 41.9 % | 78.9 % |
| Trigram reference | 36.7 % | 67.0 % |

| Minute | 0-2 | 2-5 | 5-10 | 10-15 | 15-20 | 20-30 | 30+ |
|---|---|---|---|---|---|---|---|
| Transformer top-1 | 92.6 % | 76.9 % | 47.9 % | 43.9 % | 38.3 % | 38.9 % | 25.9 % |
| Trigram top-1 | 75.1 % | 61.7 % | 39.4 % | 41.6 % | 34.8 % | 35.0 % | 21.9 % |
| Steps | 936 | 867 | 2,216 | 3,602 | 3,026 | 5,978 | 4,406 |

First observations to follow up in Phase I:
- **Largest gain in the opening.** In minutes 0-5 the transformer is 15-18 pp better. The explorer shows why in
  single nodes: after *Villager › House › Villager* the transformer hits 92 %, the trigram 0 %, because the
  trigram context (House, Villager) occurs throughout the game and points to Farm. The transformer knows where
  in the game it is (position, minute).
- **Mid game (10-15 min) the gap almost closes** (44 % vs 42 %): there the next step depends on things neither
  model sees well (opponent, map, unit composition).
- **Research is predicted worst** (15 % top-1 vs 62 % for queued units); by age: 70 % in Dark Age, 22 % in Imperial.
- **Calibration:** slightly overconfident in the middle range (predicted 0.49 → 45 % correct, 0.68 → 60 %).
- **Impossible suggestions are rare and come with low confidence.** 1.2 % of the top-1 predictions (256 of 21,031)
  fail the plausibility check, against 0.7 % false alarms on the real actions (`true_plausible` = 99.3 %), so the
  model adds only ~0.5 pp beyond the checker's noise. 31 of the 256 are in fact the true action (checker false
  alarms). The implausible ones have a mean top-1 probability of 0.30 (all: 0.45) and are right only 12 % of the time.
  Reasons: age 117, spending 97, civ 21, prerequisite 17, producer 5. Typical case: Knight, Stable or Castle Age
  suggested while the session is estimated to be one age lower, i.e. the model anticipates the age-up. Because the
  log misses some age commands (README, "Known limitations"), part of these are checker errors; the age-up
  boundary is a Phase I lead to look at in detail. Imperial Age has the most flags (3.8 %), mostly spending,
  where the checker's spending estimate is weakest.

## Open ideas for the explorer (handover)

The explorer covers the opening (about the first 15 distinct actions). Ideas for whoever continues it, in order of
expected value for the Phase I findings. Data for all of them is already in `explorer/data.js` or can be added in
`node_record()` of `scripts/12_explorer_data.py`.

1. **Conspicuous prefixes.** A list of nodes ranked by number of transformer errors (n × (1 − model_acc)) and of
   nodes where the trigram beats the transformer; clicking jumps to the node.
2. **"What instead?"** For wrong predictions at a node: predicted vs. true action (small confusion table). Shows
   whether errors are near misses (Mill instead of Lumber Camp) or far off. Needs per-session top-1 per node.
3. **Age and minute distribution** per node instead of the median only; explains errors when one prefix occurs at
   different points of the game.
4. **Adjustable minimum sessions** (3 / 5 / 10): deeper but noisier tree.

Mid and late game, where the model is weakest, are better analysed with session timelines from
`predictions.parquet` than with the prefix tree.

## predictions.parquet

One row per target run of a test session (21,031 rows, 190 sessions).

| Column | Meaning |
|---|---|
| `split`, `session_id`, `match_id`, `step` | Keys; `step` = index of the run within the session (0 = first action) |
| `first_seq`, `last_seq` | `seq` range of the run in `decisions.parquet` |
| `start_time_s`, `minute`, `age`, `run_length` | Context of the true run (age from the pipeline, not a model input) |
| `true_token`, `true_object`, `true_action` | The real next different action |
| `top5_tokens`, `top5_probs`, `top1_token`, `top1_object` | Transformer prediction |
| `p_true`, `true_rank`, `entropy` | Probability and rank (1 = best) of the true action, entropy of the distribution |
| `is_top1`, `is_top5` | Hit flags |
| `tri_top5_tokens`, `tri_top5_probs`, `tri_top1_token`, `tri_p_true`, `tri_level`, `tri_is_top1`, `tri_is_top5` | Trigram reference |
| `true_plausible`, `top1_plausible`, `top1_reasons`, `top5_n_plausible` | Plausibility join (D8) |
| `checkpoint` | Which checkpoint produced the row |
