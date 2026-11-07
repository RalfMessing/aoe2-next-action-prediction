Phase 2 is done and I've stopped before Phase 3. Every name the log uses is now either mapped to a game object or explicitly decided, and all 17 spot checks agree with what I expected.

scripts/03_load_static.py applies the schema and loads everything in about 10s. Two runs with different hash seeds produced a byte-identical database. Details are in reports/static_data.md.

Row counts

┌─────────────────────────────┬───────┬──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│            Table            │ Rows  │                                                        Notes                                                         │
├─────────────────────────────┼───────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ game_object                 │ 481   │ 37 buildings, 187 units, 256 technologies (89 of them unit upgrades), 1 UNKNOWN; all from the 2023 tree, no 2026     │
│                             │       │ fallback needed                                                                                                      │
├─────────────────────────────┼───────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ civilization                │ 45    │                                                                                                                      │
├─────────────────────────────┼───────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ civilization_object         │ 7,330 │ Age and producer per civ                                                                                             │
├─────────────────────────────┼───────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ dependency                  │ 72    │ 57 from the tree links (including two 1-of-n groups), 15 hand-kept                                                   │
├─────────────────────────────┼───────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ name_mapping                │ 305   │                                                                                                                      │
├─────────────────────────────┼───────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ source_file                 │ 64    │ SHA-256 of every input file                                                                                          │
├─────────────────────────────┼───────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ match, player, session,     │ 0     │ Created now, filled in Phase 3                                                                                       │
│ event                       │       │                                                                                                                      │
└─────────────────────────────┴───────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

Mapping coverage

Shares are of player decisions: Command events for units and research, Start Build for buildings.

┌───────────────────────────┬─────────────┬────────────┬───────────────────────┬───────────────┬───────────────────────────────────┐
│          Action           │    Exact    │ Normalized │        Manual         │    UNKNOWN    │               Open                │
├───────────────────────────┼─────────────┼────────────┼───────────────────────┼───────────────┼───────────────────────────────────┤
│ Queue (73 names)          │ 71 (97.1%)  │ 0          │ 1 (2.9%, Camel Scout) │ 1 (24 events) │ 0                                 │
├───────────────────────────┼─────────────┼────────────┼───────────────────────┼───────────────┼───────────────────────────────────┤
│ Research (170 names)      │ 163 (95.3%) │ 3 (4.4%)   │ 2 (0.1%)              │ 2 (0.3%)      │ 0                                 │
├───────────────────────────┼─────────────┼────────────┼───────────────────────┼───────────────┼───────────────────────────────────┤
│ Build (26 names)          │ 25 (100%)   │ 0          │ 0                     │ 1 (66 events) │ 0                                 │
├───────────────────────────┼─────────────┼────────────┼───────────────────────┼───────────────┼───────────────────────────────────┤
│ Create Initial (36 names) │ 19          │ 0          │ 4 gate variants       │               │ 0 (13 marked as not game objects) │
└───────────────────────────┴─────────────┴────────────┴───────────────────────┴───────────────┴───────────────────────────────────┘

Ambiguous names, mapped to the generic ID: Spearman → 93, Konnik → 1225, Town Center → 109. Pikeman and Halberdier stopped being ambiguous, because the normal and Donjon versions share one upgrade tech.

Open manual mappings: none.

Prerequisite spot check

The expected values come from my general game knowledge and weren't checked in the game itself.

┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬─────────────┐
│                                                             Check                                                             │   Result    │
├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────┤
│ Archery Range ← Barracks, Stable ← Barracks, Market ← Mill, Siege Workshop ← Blacksmith, Farm ← Mill                          │ all agree   │
├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────┤
│ Heavy Plow ← Horse Collar, Iron Casting ← Forging, Arbalester ← Crossbowman, Hussar ← Light Cavalry, Town Patrol ← Town Watch │ all agree   │
├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────┤
│ University and Blacksmith have no prerequisite besides age                                                                    │ both agree  │
├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─────────────┤
│ Age and producer of Villager, Crossbowman, Monk, Loom and Castle Age                                                          │ all 5 agree │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴─────────────┘

Bugs I fixed during this phase

- Alternatives stored as AND: Farm and Market required both Mill and Folwark. They are now 1-of-2 groups.
- A parser bug: a commented-out line in techtree.js was read as a real link. Comments are now stripped first.
- Upgrade detection: "upgrade-or-base" units were treated as upgrades everywhere. A unit now counts as a researched upgrade only if no building trains it directly.
- Missing replacement producers: the Fortified Church, Mule Cart and Folwark are now used as per-civ replacement producers (15 rows).

Things to check

- Age-up rules: data/manual/manual_dependencies.csv holds them, and they come from my game knowledge, not the files. Two rows are assumptions: the Folwark counts toward Feudal Age, and the Fortified Church toward Imperial Age. Mule Cart, Krepost and Donjon aren't modelled at all.
- Xolotl Warrior: the 2023 tree lists it for Aztecs, Incas and Mayans at a Stable they don't have. I left it as is, with a note in source_detail.
- Town Center: using the generic ID 109 means a TC built in any age counts as plausible.

Files

- New: scripts/02_schema.sql, scripts/03_load_static.py, data/manual/manual_mapping.csv, data/manual/manual_dependencies.csv, reports/static_data.md.
- Changed:
  - scripts/aoe2_rules.py now holds the event-type grammar and strips comments.
  - 01_explore.py imports the grammar from there and has been re-run.
  - The README has the runtimes, a "Database decisions" section and a "Known limitations" section.

The schema already contains the Phase 3 tables (match, player, session, event), including the flags, in_vocabulary and the nullable object_ref, but they are still empty. Running 03_load_static.py resets the whole database, so Phase 3 has to run after it. Nothing is committed.