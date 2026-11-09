# Next-Action Prediction in Age of Empires II

Group project for **AI and Visualization** (JKU Linz, WS 2026) – Group *Vieso*

We train a small transformer to predict a player's next action in Age of Empires II and use visual analytics to understand **when** it fails (Phase I) and **why** (Phase II).

---

## Task

We predict a player's next action (build, research, train) from their previous actions, using an object-centric event log of 1,000 matches. The model outputs a probability distribution over all action types (top-k predictions).

Attack actions and match outcomes are not part of the dataset and are therefore excluded.

## Data

| Source | Description |
|---|---|
| [Zenodo 13365584](https://zenodo.org/records/13365584) | OCEL 2.0 event log of 1,000 AoE2 matches (~2.4M events). We use the SQLite version. |
| [aoe2techtree at commit `9d2f8987b2852695e997cc8ca4bee077108d1fbc`](https://github.com/SiegeEngineers/aoe2techtree/tree/9d2f8987b2852695e997cc8ca4bee077108d1fbc) (2023-12-15) | **Primary game rules**: the tech tree as of the end of the replay period. `data.json`, the English string table and `js/techtree.js` (producers, prerequisite links) are stored in `data/raw/aoe2techtree_2023/`. |
| [aoe2techtree.net/data/data.json](https://aoe2techtree.net/data/data.json) (2026) | **Fallback** for fields the 2023 files lack. `data/raw/data.json` plus the string table and per-civ tree files in `data/raw/aoe2techtree/`. |

data.json and both tech tree snapshots are included. `scripts/00_fetch_techtree.py` downloads them again (the 2023 files from the commit above) and writes a `manifest.json` with SHA-256 hashes into each folder.

## Model

- **Main model:** small causal (decoder-only) transformer, ~0.5–1M parameters, trained from scratch in PyTorch.
  - Input: the last up to 128 actions plus game time
  - Output: probability distribution over ~269 action types
- **Baseline:** trigram (Markov) model with backoff, which only sees the last two actions. Where the transformer beats the baseline, it must be using longer context.

## Analysis plan

### Phase I – Model behaviour (black box)

We compare predictions with the actual next actions and analyse when the model fails, e.g.:

- after age-ups
- for rare actions (unique units)
- for specific openings

Main views:

- **Prefix tree of build orders**, coloured by error rate
- **Session timelines** (correct / wrong predictions over game time)
- **Calibration plots** (confidence vs. actual accuracy)
- Comparison with the trigram baseline

### Phase II – Model internals

To explain the patterns found in Phase I:

- Attention patterns
- Action embeddings (UMAP)
- Probing of hidden states for the current age
- Training checkpoints

## Model, predictions and explorer (Phase I, AI-aided pair)

Built on top of the training export; decisions, results and the column list of `predictions.parquet` are in
[`doc/model.md`](doc/model.md), the AI interaction record in [`ai-interactions/`](ai-interactions/).

```bash
.venv/bin/python scripts/10_train.py          # transformer, checkpoints in models/transformer_v1/ (~12 min CPU)
.venv/bin/python scripts/11_predict.py        # data/processed/predictions.parquet incl. plausibility join
.venv/bin/python scripts/12_explorer_data.py  # explorer/data.js; then open explorer/index.html in a browser
```

First run (test split, repetitions collapsed): transformer top-1 41.9 % / top-5 78.9 %, trigram reference on the same
target 36.7 % / 67.0 %. `models/` and `*.parquet` are not committed; share them via the team folder.

## Repository structure

```
├── data/
│   ├── raw/            # OCEL SQLite from Zenodo (not committed), data.json, tech tree snapshots
│   ├── manual/         # hand-kept mappings, rules and civ group labels (with evidence)
│   └── processed/      # aoe2.db and the training export (generated, not committed)
├── doc/                # setup_prompt.md: specification of the database build
├── reports/            # generated reports of each build step
├── scripts/            # database build, training
├── notebooks/          # Phase I / Phase II analyses
└── README.md
```

## Setup

Requirements: Python 3.11+ whose `sqlite3` module ships SQLite 3.38 or newer (check with `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`), about 2 GB of free disk space, and internet only if the tech tree snapshots are missing. Scripts 00–07 use only the standard library; the training export (08) needs `pyarrow`.

1. Clone the repository and create the environment:
   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```
2. Download `age_of_empires_ocel2.sqlite` (~890 MB) from [Zenodo](https://zenodo.org/records/13365584) and place it at `data/raw/age_of_empires_ocel2.sqlite`. Everything else is in the repository: `data/raw/data.json`, the tech tree snapshots in `data/raw/aoe2techtree_2023/` (primary) and `data/raw/aoe2techtree/` (2026 fallback), and the hand-kept decisions in `data/manual/`.
3. Build everything:
   ```bash
   .venv/bin/python scripts/run_all.py
   ```
   This takes about 80 s and ends with `11 of 11 checks passed` from `07_validate.py`. It runs the steps below in order and stops at the first failure.

   | Step | Script | Purpose | Runtime |
   |---|---|---|---|
   | – | `00_fetch_techtree.py` | Only if `data/raw/aoe2techtree*/` is missing: download the pinned tech tree files | ~1 min |
   | – | `01_explore.py` | Optional: profile the sources, write `reports/exploration.md` (not part of `run_all.py`) | ~45 s |
   | 1 | `03_load_static.py` | Apply `02_schema.sql`, load the game rules, write `reports/static_data.md` | ~15 s |
   | 2 | `04_load_replays.py` | Load matches, players, sessions, events and raw E2O links, write `reports/replay_load.md` | ~40 s |
   | 3 | `05_civ_inference.py` | Narrow down each session's possible civilizations, write `reports/civ_inference.md` | ~2 s |
   | 4 | `06_resources.py` | Starting resources, age over time, resource snapshots, plausibility validation, write `reports/resources.md` | ~15 s |
   | 5 | `07_validate.py` | Summary with pass/fail checks and known limitations, write `reports/validation.md` | ~10 s |
   | 6 | `08_export.py` | Training export (see below) | ~2 s |

   Runtimes are from one laptop run on the full log. All scripts are idempotent and never modify the source files. `03_load_static.py` on its own recreates all tables and so **wipes the replay data**; always rebuild with `run_all.py`. The database is about 730 MB, most of it the raw E2O links (`event_object_link`).

Outputs in `data/processed/` (not committed):

| File | Content |
|---|---|
| `aoe2.db` | One SQLite database with game rules, replay events, civ groups, ages and resource snapshots |
| `decisions.parquet` | One row per player decision (615,296): `session_id`, `match_id`, `seq` (index within the session), `token_id`, `object`, `action`, `time_s`, `minute`, `age`, `age_source`, `civ_group_id`, the four session flags, plus source ids |
| `vocab.csv` | 265 tokens: `token_id` (0-based, by frequency; no PAD/BOS reserved), `game_object_id`, `name`, `type`, `action`, `count` |
| `split.csv` | `train`/`val`/`test` = 800/100/100 matches, split by match (both players in the same split), seed 42 |

The export excludes nothing and collapses nothing: filter on the flag columns and decide on repetitions model-side. 63.9 % of decisions repeat the previous token of the same session (mostly runs of Villager).

> Everyone works with the same `aoe2.db` and export. Do not write your own parsing of the raw data.

## Database decisions

The evidence for each point is in `reports/exploration.md` and `reports/static_data.md`.

- **Rule source.** Game rules come from aoe2techtree at commit `9d2f898` (2023-12-15). The 2026 files are only a fallback for log names that 2023 cannot map (currently none) and a cross-check. Every static row has `source` (`2023`, `2026` or `manual`) and `source_detail`.
- **Unit upgrades** such as Crossbowman exist twice in `game_object`: as a `UNIT` and as the `TECHNOLOGY` that researches it (`upgrades_to` points to the unit). Research events map to the technology.
- **Name mapping** (`name_mapping`, per action) uses the methods `exact`, `normalized` (case and punctuation ignored, e.g. `DoubleBit Axe`) and `manual` (`data/manual/manual_mapping.csv`, each row with evidence). Decisions taken:
  - Queued `Camel Scout` → Camel Rider. A starting Camel Scout (CREATE_INITIAL) stays Camel Scout, a Gurjaras signal.
  - `Scutage` → Hauberk (tech 757), `Heavy Cav Archer` → Heavy Cavalry Archer.
  - The placeholder names `None` and the empty suffix → one `UNKNOWN` game object. The events are kept.
  - Animals and unknown starting objects (`Horse A` etc.) are marked as not being game objects.
- **Ambiguous names map to a generic ID:** the candidate most civs have, then the lowest tree ID. Spearman → 93 (not the Sicilian Donjon spearman 1786), Konnik → 1225 (Castle, not the Krepost version 1254). Pikeman and Halberdier are not ambiguous after this, because the normal and the Donjon versions share one upgrade tech.
- **Town Center**: building a TC (BUILD) → 621, the additional TC (Castle Age; Feudal for Cumans via `data/manual/manual_civ_overrides.csv`, from the 2023 civ help text). The starting TC (CREATE_INITIAL) → 109, the Dark Age TC. Plausibility checks must use the per-civ age in `civilization_object`. On maps without a starting TC, the first TC is built in the Dark Age.
- **Age and producer** vary by civ and are stored per civ in `civilization_object`. `game_object` holds the earliest age and the most common producer. Buildings are produced by the Villager (Fish Trap by the Fishing Ship). Where a civ lacks the usual building, the replacement shown by the tree links is used (Fortified Church for Monastery, Mule Cart for Lumber and Mining Camp, Folwark for Mill).
- **Prerequisites** (`dependency`) come from the links in the 2023 `js/techtree.js`, plus `data/manual/manual_dependencies.csv` for rules the layout does not encode: the age-up building requirements (e.g. Imperial Age needs 2 Castle Age buildings or a Castle). Rows sharing `req_group` are an "n of" group. Alternatives such as Farm after Mill *or* Folwark are 1-of-n groups.
- **Events whose OCEL objects are missing** from the source's `object` table (`UO` IDs, 3 % of events) are kept; `event.object_ref` is a plain text column.
- **Vocabulary.** `in_vocabulary = 1` only for COMMAND of QUEUE and RESEARCH and START of BUILD, without UNKNOWN: 265 game objects. CREATE_INITIAL, SET_GATHER_POINT, GATHER and all simulated START/COMPLETE events have `in_vocabulary = 0`.
- **Time.** `event.time_s` is seconds since the match's first event, in game time (Villager Start→Complete = 25 s = the game's train time). `event.seq` orders events within a second.
- **Main object of an event** (`event.object_ref`): the building or unit created (BUILD, CREATE_INITIAL), the villager (GATHER), or the producing building (QUEUE, RESEARCH, SET_GATHER_POINT). All raw links are kept in `event_object_link`.
- **Suspicious sessions are flagged, never deleted:** `short_match` (match spans < 300 s), `no_commands` (no COMMAND event), `outlier_commands` (more COMMAND events than the 99th percentile, currently > 1204), `has_unmapped_events` (a QUEUE/RESEARCH/BUILD event mapped to UNKNOWN).
- **Civ inference** (Phase 4). Candidates = civs whose `civilization_object` contains every in-vocabulary object the session used (ages ignored), intersected with start signals from `data/manual/start_signals.csv` (6 villagers → Chinese, 4 → Mayans, Llama → Incas, Eagle Scout → Aztecs/Incas/Mayans, Camel Scout → Gurjaras). A start signal counts only if the opponent does not show it too; otherwise it is treated as map-dependent. An empty set goes to the group `none`, and `session.civ_conflict` names the evidence that emptied it.
- **Civ group labels** live in `data/manual/civ_group_labels.csv` (key = sorted civilization IDs). `05_civ_inference.py` only appends rows for new keys and never rewrites existing labels; group ids follow the file order. Delete a row only if you really want that label regenerated. Labels: the civ name (1 civ), names joined by `/` (2-3), a description by shared or missing objects (4-10), `broad (N civs)` (11+).
- **Replacement buildings** are logged under the base name: a Polish Folwark appears as "Mill", an Armenian/Georgian Fortified Church as "Monastery" (verified with sessions that train Obuch or Warrior Priest). `data/manual/replacement_equivalents.csv` makes a logged base name count as *base or replacement* in civ inference and plausibility. Mule Cart is not included: there is no sign that it appears as Lumber or Mining Camp.
- **Resources** (Phase 5, `06_resources.py`, all `estimated = 1`). Costs are deducted at COMMAND for units and research (configurable: `--deduction-phase START`) and at START for buildings. Starting resources are 200/200/100/200 (`--start`), adjusted only for sessions narrowed to one civ (`data/manual/start_resources.csv`, from the 2023 help texts). `resource_snapshot` holds cumulative spending, the minimum gathered (spending beyond the start) and villagers trained so far at every vocabulary event.
- **The current age** (`session_age`) is the earlier of (first `Command Research <Age>` + research time from the 2023 tree) and (first use of an object that needs the age), never `Complete Research`. The source is stored. Object evidence matters: 5-10 % of sessions reach an age without any logged age command.
- **Plausibility** (`scripts/aoe2_plausibility.py`): `is_plausible(session_id, time_s, game_object_id)` returns `(ok, reasons)` with the reasons civ, age, prerequisite (incl. groups), producer and spending. Evidence only counts if it happened before `time_s`. The spending check uses an upper bound of 0.5 resources per villager-second (`--max-gather-rate`), calibrated as the smallest value that keeps spending alarms on real decisions under 0.5 %. It is a loose sanity bound, not an income estimate, and catches few implausible actions. Khmer are exempt from building requirements and identified Malay age up 66 % faster (2023 help texts). See `reports/resources.md` for the false-alarm rate on real decisions and the catch rate on random substitutes.

## Known limitations

- **Simulated times.** START and COMPLETE times were produced by the dataset authors, not observed. Each type has one fixed duration, 0 s for many, which is what a lookup table with missing entries would produce. Only the player decisions are reliable times: COMMAND, and START for buildings.
- **Research durations** in the log mostly match no rule version (13 % of research types match the 2023 or 2026 tree), so COMPLETE RESEARCH times are unusable.
- **Likely mislabeled civs.** Celts, Dravidians and Hindustanis (also Romans and Georgians) have no unique unit or tech anywhere in the log. Their events are probably mislabeled or dropped, so these civs cannot be identified.
- **Not in the log:** gathered amounts, unit deaths, damage, attacks and match outcomes. Villager counts are "trained so far", not alive. Gather events are task assignments, not income. Walls are never built in the log.
- **Civ, map and Elo** are not recorded and can only be inferred.
- **Game rules from general knowledge.** The age-up rules in `manual_dependencies.csv` come from general game knowledge, not from the tree files. That the Folwark (Poles) counts toward Feudal Age and the Fortified Church (Armenians, Georgians) toward Imperial Age is an assumption. Mule Cart, Krepost and Donjon are not modeled as age-up buildings.
- **No civ bonuses on costs or times.** Costs and times are the generic values (e.g. no cheaper Archery Range for Cumans, no faster villagers). Only the Cuman Feudal TC is overridden per civ.
- **No population data.** Neither data.json version has population cost or housing, so pop-cap checks are impossible.
- **Missing age commands.** The log lacks the age-up command in many sessions that clearly reached the age (e.g. 93 sessions reach Castle Age without a Castle Age command). The age estimate falls back to object evidence; age-based plausibility can still raise false alarms at the first use of an age-gated object.
- **Spending is overestimated** when production was cancelled (not in the log), and income from trade, relics, tributes and the market is unknown. Civ cost bonuses are not modeled.
- **Tree quirk.** The 2023 tree lists the Xolotl Warrior for Aztecs, Incas and Mayans at a Stable they do not have. It is kept as is, with a note in `source_detail`.

## Credits

Dataset: Liss, L., Elbert, N., Flath, C. M., & van der Aalst, W. M. P. (2024). *Object-Centric Event Log for Age of Empires Game Interactions*. Zenodo. https://doi.org/10.5281/zenodo.13365584 (CC BY 4.0)