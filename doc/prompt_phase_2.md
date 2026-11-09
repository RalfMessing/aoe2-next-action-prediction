OK for Phase 2 with the 2023 tree as primary source (2026 only as fallback/cross-check).

Answers:
1. Yes: queued `Camel Scout` → Camel Rider, unless it is a starting unit (Create Initial), which stays a Gurjaras signal.
2. Yes: Scutage → Hauberk (tech 757), Heavy Cav Archer → Heavy Cavalry Archer, both in manual_mapping.csv with a short evidence note.
3. Per-row `source` + `source_detail` is enough.
4. Yes: per-civ `required_age` and `produced_at` on civilization_object (earliest age on game_object); flag suspicious sessions instead of deleting (short_match <300 s, no_commands, outlier_commands, has_unmapped_events).

Also apply (from Phase 1):
- Mapping methods: exact | normalized | manual.
- Placeholder names (`None`, empty suffix) → one `UNKNOWN` game object; keep the events.
- Ambiguous names (Spearman, Town Center, …): generic ID, decision documented in README.
- Events with objects missing from `object` (UO IDs): keep, FK nullable.
- Persist Create Initial and Set Gather Point with `in_vocabulary = 0`.
- Prerequisites: from techtree.js plus a small hand-kept `manual_dependencies.csv` for rules the layout doesn't encode (age-up building requirements etc.). Spot-check 10 prerequisites against game knowledge and list them in the report.
- Resource deduction default: Command for units/research, Start Build for buildings (configurable).
- Current age: Command Research <Age> + research time from the 2023 tree, not Complete Research.
- README: Start/Complete times are simulated; research durations in the log mostly don't match any rule version; Celts/Dravidians/Hindustanis likely mislabeled; no gathered amounts or deaths.

Stop after Phase 2 with a short report: row counts per table, mapping coverage, open manual mappings, prerequisite spot-check.

