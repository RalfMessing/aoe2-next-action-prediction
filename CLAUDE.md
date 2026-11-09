# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

University group project (AI and Visualization, JKU Linz, group *Vieso*). A small causal transformer predicts a player's next action (build, research, train) in Age of Empires II, and visual analytics explain when it fails (Phase I: black-box behaviour) and why (Phase II: model internals). See `README.md` for the model specification and analysis plan.

## Current state

The data pipeline is complete (branch `data-pipeline`): `scripts/00`–`08` build `data/processed/aoe2.db` and the training export, run with `.venv/bin/python scripts/run_all.py` (see README "Setup"). The model code exists (branch `model`): `scripts/aoe2_sequences.py` (filtering, collapsing, windows), `aoe2_model.py`, `aoe2_trigram.py`, `10_train.py`, `11_predict.py`, `12_explorer_data.py` and `explorer/`. Decisions and results are in `doc/model.md`. `requirements.txt` holds pyarrow, numpy, pandas and torch (CPU is enough).

## Data pipeline

`doc/setup_prompt.md` is the specification. Numbered, idempotent scripts in `scripts/` (`00_fetch_techtree.py` to `08_export.py`, plus `02_schema.sql`, the shared modules `aoe2_rules.py` and `aoe2_plausibility.py`, and `run_all.py`) build one cleaned database, `data/processed/aoe2.db`, that joins replay events with tech-tree rules. Everyone uses this database rather than parsing the raw data themselves.

Its rules are to never modify source files, to prefer stdlib `sqlite3` with `ATTACH` + `INSERT … SELECT` for bulk data, and to flag estimated values. Decisions taken along the way are in the README ("Database decisions", "Known limitations") and `data/manual/*.csv` (each row with evidence). `data/manual/civ_group_labels.csv` is append-only.

"Phase" means different things in the two documents: the README's Phases I and II are analysis stages, while the Phases 1 to 6 in `doc/setup_prompt.md` are database build steps.

## Data

Everything is in `data/raw/`:

| File | Contents |
|---|---|
| `age_of_empires_ocel2.sqlite` | Full OCEL 2.0 log of 1,000 matches (~890 MB) |
| `age_of_empires_ocel2_10_match_filter_zenodo/` | 10-match sample as SQLite, JSON and XML (14 MB SQLite). Use it for quick iteration. |
| `data.json` | Tech tree from aoe2techtree.net, used to map actions to categories and ages |

The OCEL SQLite schema has core tables (`event`, `object`, `event_object`, `object_object`, `event_map_type`, `object_map_type`) plus one table per event or object type, named `event_<TypeMap>` (for example `event_CommandQueueArcher`). Type-to-table names come from the `*_map_type` tables. That gives hundreds of tables (392 in the 10-match sample), so query the map tables rather than hard-coding names. IDs encode their context: `M…` is a match, `P…` a player, `S<match>_<player>` a session.

Attack actions and match outcomes are not in the dataset.

Raw data and generated files are not committed: `.gitignore` covers the contents of `data/raw/`, `*.sqlite`, `*.db` and `*.parquet`. The exception is `data/raw/data.json`, which is committed so everyone uses the same tech tree version.

## Course files

`team-info.json`, `workload.json`, `ai_reflection_midpoint.md` and `ai-interactions/` are project-administration files, not code. Leave them alone unless asked to change them.
