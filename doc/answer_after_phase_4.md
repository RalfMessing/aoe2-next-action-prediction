Phase 4 is done and I've stopped before Phase 5. Every session now has a civ group: 29.1% narrow to exactly one civ, and only 4 sessions match no civ at all.

A full rebuild (python3 scripts/run_all.py, about 55s) reuses all 543 group labels unchanged. The details are in reports/civ_inference.md.

Housekeeping

- .gitignore: now ignores everything in data/processed/ except the placeholder file.
- data/manual/civ_group_labels.csv: has 543 groups. Script 05 only appends rows for new keys and checks that each key still means the same civs. I generated the file three times while improving the label wording, which was the initial creation; from now on it's append-only.
- data/manual/start_signals.csv: 5 signals, each with evidence. The villager and Llama signals quote the 2023 civ help texts. Eagle Scout and Camel Scout cite which civs have them in the 2023 tree plus the log pattern.
- Schema: session has two new columns, civ_conflict and civ_conflict_object_id.

Results

Group sizes:

┌────────────────┬──────────────┬────────────────────┐
│ Possible civs  │ Objects only │ With start signals │
├────────────────┼──────────────┼────────────────────┤
│ 0 (group none) │ 4            │ 4                  │
├────────────────┼──────────────┼────────────────────┤
│ 1              │ 471 (23.6%)  │ 582 (29.1%)        │
├────────────────┼──────────────┼────────────────────┤
│ 2–10           │ 311          │ 260                │
├────────────────┼──────────────┼────────────────────┤
│ 11 or more     │ 1,214        │ 1,154 (57.7%)      │
└────────────────┴──────────────┴────────────────────┘

Start signals are used only when the opponent doesn't show the same one:

┌─────────────┬───────────────┬────────────────────────────────┐
│   Signal    │ Sessions used │ Ignored (both players have it) │
├─────────────┼───────────────┼────────────────────────────────┤
│ Eagle Scout │ 113           │ 6                              │
├─────────────┼───────────────┼────────────────────────────────┤
│ 4 villagers │ 44            │ 0                              │
├─────────────┼───────────────┼────────────────────────────────┤
│ Llama       │ 38            │ 118                            │
├─────────────┼───────────────┼────────────────────────────────┤
│ Camel Scout │ 30            │ 0                              │
├─────────────┼───────────────┼────────────────────────────────┤
│ 6 villagers │ 22            │ 24                             │
└─────────────┴───────────────┴────────────────────────────────┘

The 4 sessions with no candidate were emptied by Mill (2) and Monastery (2).

Top groups: the largest are broad, for example "broad (41 civs)" with 135 sessions, all civs except those without a Lumber Camp. Among the top 15, the single-civ groups are Mayans 44, Spanish 39, Incas 38, Mongols 38, Portuguese 31 and Gurjaras 30. Most of those come from unique units or start signals.

Single-civ sessions: 39 civs appear at least once. Six never narrow to one civ: Armenians, Celts, Georgians, Hindustanis, Poles and Romans. That fits the suspected mislabeled or missing events.

The 208 early TC builds (in sessions with a starting TC):

┌───────────────────────────────────────────┬────────┬───────────────────────────────────┐
│                Explanation                │ Builds │             Sessions              │
├───────────────────────────────────────────┼────────┼───────────────────────────────────┤
│ Cumans possible                           │ 92     │ 69 (2 of them can only be Cumans) │
├───────────────────────────────────────────┼────────┼───────────────────────────────────┤
│ Fast age-up civ possible: Persians        │ 20     │ 18                                │
├───────────────────────────────────────────┼────────┼───────────────────────────────────┤
│ Fast age-up civ possible: Malay           │ 13     │ 9                                 │
├───────────────────────────────────────────┼────────┼───────────────────────────────────┤
│ Unexplained: no Castle Age command at all │ 82     │ 31                                │
├───────────────────────────────────────────┼────────┼───────────────────────────────────┤
│ Group none                                │ 1      │ 1                                 │
└───────────────────────────────────────────┴────────┴───────────────────────────────────┘

So 125 builds are compatible with a civ rule, but the civ isn't confirmed. The 82 unexplained builds come from sessions that never command Castle Age, which suggests that command is missing from the log for those players.

Sanity check: in 126 matches both players narrowed to one civ, 6 of them mirror matches. The evidence reads correctly, for example Mongols (Mangudai) vs. Persians (Kamandaran), Incas (Llama start, Eagle Scout) vs. Mayans (4 villagers), and Bulgarians (Krepost) vs. Vikings (Berserk).

Label quality

- 1 civ: the civ name.
- 2–3 civs: the names joined with "/", for example "Byzantines / Ethiopians / Koreans".
- 11 or more: "broad (N civs)". The description says "All civs except …" when only a few are missing.
- 4–10 civs: 105 groups. 32 have a combination label ("Heavy Camel Rider + Halberdier civs"), 3 "X civs without Y" and 1 an exact single object. The remaining 69 fall back to "N civs with X" because describing them exactly would take four or more terms. Their description lists every candidate civ.

Files

- New: scripts/05_civ_inference.py, data/manual/start_signals.csv, data/manual/civ_group_labels.csv, reports/civ_inference.md.
- Changed: .gitignore, scripts/02_schema.sql, README.md (run order, plus how civ inference and labels work).

Nothing is committed.

