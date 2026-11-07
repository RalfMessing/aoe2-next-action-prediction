Phase 1 is finished, and I've stopped here for your OK

The log can support the database, but only its player decisions (Command events and Start Build) are reliable. Its Complete times and most unit Start times are derived, and civ inference will rarely narrow a session to one civ.

The report takes about 42s to generate, and re-runs are byte-identical. The source database's size and modification time are unchanged, and nothing is committed.

Surprises

1. Complete and most Start times look computed, not observed (most important).
- Every unit and research type has exactly one duration across all its events. Villager is always 25s, Feudal Age always 130s.
- 92 of 170 research types take exactly 0s, including Imperial Age, Wheelbarrow and Town Watch. That's 25% of research events. 21 unit types and 7 building types are also 0s.
- These durations match data.json for only 30 of 71 mapped unit types and 22 of 163 research types.
- Unit Start times behave like a simulated queue: 91.5% begin in the same second the previous unit finished.
- Command, Start and Complete counts are identical for every type, so cancelled production isn't visible.
- This is my inference from the numbers; I didn't read the dataset's generation code.

Consequences:
- The model's action sequence should use Command events, plus Start Build for buildings.
- "Current age" can't rely on Complete Research.
- Resource deductions shouldn't default to the Start phase as the spec suggests. As far as I know the game charges when you queue, so I'd default to Command (Start Build for buildings).

2. Some spec assumptions don't hold.

┌──────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                  Spec said                   │                                               Actual                                               │
├──────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Command Build … and Start Production … exist │ Neither exists; it's Start Queue …, and Start Build is the only build decision                     │
├──────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ About 30 object types                        │ 120, and 90 of them are never linked to an event                                                   │
├──────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Civ, map, Elo and winner are empty           │ The columns don't exist at all                                                                     │
├──────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Qualifiers on links                          │ Always an empty string                                                                             │
├──────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Only player input links to Player            │ Start Build and all Set Gather Point and Create Initial events also do, as do 35% of Gather events │
└──────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────┘

3. Other limits in the log:
- Not recorded: deaths and gathered amounts, so the villager count is only "trained so far". Walls are never built in the log and only exist as starting objects.
- Undeclared objects: 29,483 UO object IDs are missing from object, which touches 3.1% of events.
- Thin matches: 55 of 1,000 matches span under 5 minutes, and 70 sessions have no Command events.
- Outliers: one match has 15,970 and 11,483 commands for its two players, against a median of 156 per session.

4. data.json is from 2026, the replays from 2023.
- It has 56 civs. I assume 11 are newer than the replays, based on my own knowledge and the fact that none of their unique items appear in the log.
- It has no age, producer, prerequisite or display name fields, and LanguageNameId mostly doesn't resolve in the string table.
- I found the string table, plus per-civ tree files that supply all of those fields. They come from aoe2techtree.net and match your data.json byte for byte.
- link_id only covers what the tree draws, so rules like the age-up requirements still need a small hand-kept CSV.

5. Civ narrowing will be weak. About 60% of sessions keep 11 or more possible civs, and about 23% narrow to exactly one. These figures use the camel mapping in question 1 below.

┌────────────────┬──────────────────────────────┬────────────────────┐
│ Candidate civs │ Sessions as logged (45 civs) │ With camel mapping │
├────────────────┼──────────────────────────────┼────────────────────┤
│ 0              │ 10.5%                        │ 1.0%               │
├────────────────┼──────────────────────────────┼────────────────────┤
│ 1              │ 21.4%                        │ 23.1%              │
├────────────────┼──────────────────────────────┼────────────────────┤
│ 2–10           │ 9.1%                         │ 16.2%              │
├────────────────┼──────────────────────────────┼────────────────────┤
│ 11+            │ 59.0%                        │ 59.7%              │
└────────────────┴──────────────────────────────┴────────────────────┘

Celts, Dravidians, Hindustanis, Romans and Georgians have no unique item anywhere in the log. Woad Raider, Urumi Swordsman and Ghulam are never queued, so those units may have been dropped or mislabeled.

Name matching

┌────────────────┬──────────────────┬───────────────────┬────────────────┐
│     Source     │ Tree name, exact │ After normalizing │ Events covered │
├────────────────┼──────────────────┼───────────────────┼────────────────┤
│ Queue          │ 71/73            │ 71/73             │ 99.6%          │
├────────────────┼──────────────────┼───────────────────┼────────────────┤
│ Research       │ 160/170          │ 163/170           │ 99.2%          │
├────────────────┼──────────────────┼───────────────────┼────────────────┤
│ Build          │ 25/26            │ 25/26             │ 100%           │
├────────────────┼──────────────────┼───────────────────┼────────────────┤
│ Create Initial │ 19/36            │ 19/36             │ 91.9%          │
├────────────────┼──────────────────┼───────────────────┼────────────────┤
│ Object types   │ 98/117           │ 98/117            │ 99.3%          │
└────────────────┴──────────────────┴───────────────────┴────────────────┘

Against data.json's internal_name alone, only 99 of 285 names match.

Still unmatched:
- Clear manual mappings: Trebuchet (1,845 events) to Trebuchet (Packed), and Heavy Cav Archer to Heavy Cavalry Archer.
- Hyphens dropped: DoubleBit Axe, ManatArms and TwoHanded Swordsman only match after normalizing, so I'd add normalized as a mapping method.
- In the log but not in the 2026 tree: Supplies (80 events), Bearded Axe, Pavise and Scutage.
- Placeholder names: None and the empty suffix cover 147 Command or Start Build events across 4 of the 269 action types in the README, so that really means 265.
- Never game objects: animals, gate states and Horse A–E, which only appear at game start or as object types. So do Bactrian Camel and Sea Tower.

Ambiguous names (one log name, several IDs): Spearman, Pikeman, Halberdier, Huskarl, Konnik, Tarkan and Town Center.

Additional event types to persist

- Create Initial (yes): it gives the starting state and adds civ evidence. For example, 6 starting villagers probably means Chinese and 4 Mayans, though that's from game knowledge. Don't treat Llama as an Inca signal, since both players have it in 59 matches, which points to the map.
- Set Gather Point (yes): these are player decisions, but keep them out of the prediction vocabulary. 63% have the target None.
- Gather (already in the schema): it records villager assignments, not amounts gathered, so it can't be used as income.

Questions before Phase 2

1. Should Camel Scout map to Camel Rider or Camel Scout? I recommend either unit for civ inference, with Camel Rider as the mapped object when the session didn't start with a Camel Scout. It's queued in 237 sessions but is a starting unit in only 30, and the log has no Queue Camel Rider. Train times are equal, so this stays unconfirmed.
2. Can Phase 2 use the tree files, even though they're from 2026? I recommend yes, restricted to the 45 civs of the 2023 game. Costs and availability will be somewhat wrong for 2023, so everything derived from them gets flagged. Nothing in the repo has 2023 rules; an old aoe2techtree commit might, but I haven't checked.
3. How should values that vary by civ be stored? 32 nodes have a different age per civ and 31 a different producer. I recommend adding a per-civ civilization_object.required_age and produced_at, with the earliest age on game_object. That goes beyond your schema, so it's your call.
4. What should happen to the suspicious sessions? I recommend flagging them rather than deleting them. Flagging keeps row counts traceable, and the model can filter on the flag.

Changes beyond the spec

- scripts/00_fetch_techtree.py: new script that pins the string table and tree files in data/raw/aoe2techtree/ (5.8 MB).
- .gitignore: one added line so that snapshot gets committed and your teammate can re-run everything.

Files are in the repo:
- reports/exploration.md
- scripts/00_fetch_techtree.py
- scripts/01_explore.py