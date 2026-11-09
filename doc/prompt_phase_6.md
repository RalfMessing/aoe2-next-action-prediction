Phase 4.1 and 5 look good. Last round of database work, then we stop here:

1. Fix: the setting `age_rule` says "max(command, object)" but the rule is "earliest of". Correct the label.
2. Optional, only if quick (<15 min): calibrate --max-gather-rate. Find the smallest value at which real decisions in unflagged sessions stay under 0.5% spending alarms; report the value and the random-substitute rate at it. If that takes longer, skip it and document the spending check as a loose sanity bound only.
3. Phase 6 (07_validate.py → reports/validation.md): one summary report with row counts source vs. target, mapping coverage, flag counts, civ inference summary, plausibility false-alarm rate, and a "Known limitations" section. Update the README so a teammate can run everything from scratch (requirements, input file locations, run_all.py, runtime).
4. Training export (08_export.py → data/processed/):
   - `decisions.parquet`: one row per in_vocabulary decision with session_id, match_id, seq index within session, token_id, object name, action, time_s, minute, current age, session flags.
   - `vocab.csv`: token_id, game_object_id, name, action, count.
   - `split.csv`: train/val/test = 80/10/10, split by MATCH (both players of a match in the same split), fixed seed.
   - Don't collapse repetitions in the export. That's a model-side preprocessing choice; just document that consecutive repeats exist (e.g. Villager).
   - Exclude nothing; the flags are columns so the model side can filter.
5. Create a branch `data-pipeline`, commit everything except data/processed/, and give me a short summary I can forward to the team.

