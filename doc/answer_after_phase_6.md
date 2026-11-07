The data pipeline is finished and committed as a7404e7 on the new data-pipeline branch. I haven't pushed it. A full rebuild takes about 80s and ends with all 11 checks passing.

The last round

1. age_rule label: it now reads earliest(command, object).

2. Gather-rate calibration: done in a few minutes, using each decision's minimum passing rate instead of repeated runs.
- The smallest rate that keeps spending alarms on real decisions in unflagged sessions under 0.5% is 0.497, so the new default is 0.5 (it was 1.0). At 0.5, real decisions get 0.47% spending alarms and random substitutes 3.4%.
- The spending check is therefore only a loose sanity bound, and the README says so. The age, civ, producer and prerequisite checks do the real work.
- Flagged sessions (outliers, cancelled production) produce most spending alarms: 5.06% of real decisions over all sessions, against 0.47% in unflagged ones.

3. 07_validate.py writes reports/validation.md: source vs. target counts, mapping, flags, civ inference, ages, the plausibility check, 11 pass/fail checks and the known limitations. It exits with an error if any check fails. The README setup section now covers the requirements, where to put the OCEL file, the venv, run_all.py, runtimes and the output files.

4. 08_export.py writes to data/processed/:

┌───────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│       File        │                                                Content                                                 │
├───────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ decisions.parquet │ 615,296 rows with the columns you listed, plus source IDs, game_object_id, age_source and civ_group_id │
├───────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ vocab.csv         │ 265 tokens, numbered from 0 by frequency, no padding or start token reserved                           │
├───────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ split.csv         │ 800/100/100 matches, split by match, seed 42                                                           │
└───────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────┘

Nothing is excluded or collapsed; 63.9% of decisions repeat the previous token.

5. The commit contains 105 files: everything except data/processed/ and .venv.

A few things I did without being asked:
- requirements.txt and pyarrow: I added a requirements.txt (only pyarrow) and installed it into the existing, gitignored .venv.
- CLAUDE.md: I updated its "Current state" section, which still said no code existed.
- Your doc/ files: the prompt and answer files in doc/ went into the commit, as part of "everything".

Summary for the team

▎ Data pipeline ready (branch data-pipeline)
▎
▎ Setup: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt, put age_of_empires_ocel2.sqlite from Zenodo into data/raw/, then run .venv/bin/python scripts/run_all.py (~80 s, ends with "11 of 11 checks passed").
▎
▎ Output in data/processed/:
▎ - aoe2.db: one SQLite database with game rules, events, civ groups, ages and resource estimates.
▎ - decisions.parquet: 615k player decisions, one per row, in session order.
▎ - vocab.csv: 265 tokens.
▎ - split.csv: 80/10/10 by match.
▎
▎ What to know:
▎ - Only decision times are real. Command times (and Start for buildings) are reliable. The log's Start and Complete times are simulated by the dataset authors, and research durations are mostly wrong.
▎ - Rules are from the end of 2023. We use the aoe2techtree commit from 2023-12-15, not today's site.
▎ - Civ, map and Elo aren't in the data. Civs are inferred: 29% of sessions narrow to one civ, about 58% stay broad (11+ candidates). Celts, Georgians, Hindustanis and Romans are never identified, probably because their events are mislabeled in the log.
▎ - Age-up commands are often missing. The current age therefore also uses the first use of age-gated objects.
▎ - Nothing is filtered. Suspicious sessions are flagged as columns (short_match, no_commands, outlier_commands, has_unmapped_events). Repeats such as Villager runs aren't collapsed either. Both are model-side choices.
▎ - There's a plausibility checker. scripts/aoe2_plausibility.py provides is_plausible(session_id, time_s, game_object_id) for checking predictions. It flags 0.6% of real decisions in unflagged sessions and 78% of random substitutes. Its spending check is only a loose bound.
▎ - Details: reports/validation.md (start here), the README sections "Database decisions" and "Known limitations", and data/manual/ (hand-made decisions, each with evidence).

