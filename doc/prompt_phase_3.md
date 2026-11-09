Phase 2 looks good. Before/with Phase 3:

Fixes to Phase 2:
1. Town Center: check the per-civ required_age for building a TC (expected: Castle Age for most civs, Feudal for Cumans). The plausibility check must use civilization_object.required_age, not game_object's. Report what 109 vs 621 actually are.
2. Pop: if the 2023 data has population cost / provided population, add game_object.pop_cost and pop_provided. If not, document as a limitation.
3. Add FK session.civ_group_id → civ_group(id) when Phase 4 creates that table.
4. Add scripts/run_all.py that runs 03 → 04 → … in order, since 03 resets the database. Mention in the README that 03 alone wipes replay data.
5. README "Known limitations": no civ cost bonuses, Mule Cart/Krepost/Donjon not modelled, Folwark/Fortified Church age-up counting is an assumption, Xolotl Warrior tree quirk.

Phase 3 (04_load_replays.py) from @setup_prompt.md :
- Fill match, player, session, event as in the schema.
- time_s = seconds since the match's first event; confirm in the report that timestamps are game time (Villager = 25 s supports that).
- Flags: short_match (<300 s), no_commands, outlier_commands (define threshold, e.g. > p99 of commands per session; report the value), has_unmapped_events.
- in_vocabulary = 1 only for COMMAND (QUEUE, RESEARCH) and START (BUILD), excluding UNKNOWN; report the resulting vocabulary size.
- Also store the raw E2O links as `event_object_link(event_id, object_ref)`. No analysis needed, just keep them.
- Validation report: source vs. target event counts per ocel_type (must add up), share of events with game_object_id, vocabulary size, one full sample session as a readable timeline (first 15 min).

Stop after Phase 3 with the report.