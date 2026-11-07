Phase 3 is done and I've stopped before Phase 4. The database now holds every replay event, and the counts match the source exactly.

python3 scripts/run_all.py rebuilds everything in about 51s (03 ≈ 14s, 04 ≈ 37s). The database is about 700 MB, mostly the raw links. Details are in reports/replay_load.md and reports/static_data.md.

Phase 2 fixes

1. Town Center. Your expectation was right for building a TC, but the 2023 tree didn't encode the Cuman exception:

┌─────┬──────────────┬───────────────────────────────────────────────────────────────────────────────────┬─────────────────────────────┬─────────────────────┐
│ ID  │  Internal    │                                    What it is                                     │    Age in the 2023 tree     │ Log name mapped to  │
│     │     name     │                                                                                   │                             │         it          │
├─────┼──────────────┼───────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────┼─────────────────────┤
│ 109 │ RTWC         │ The Dark Age TC, which players start with                                         │ 1, all civs                 │ Starting TC         │
├─────┼──────────────┼───────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────┼─────────────────────┤
│ 621 │ RTWC1X       │ The additional, buildable TC (build time 150s, which matches the longest TC build │ 3, all 45 civs, Cumans      │ TC builds           │
│     │              │  in the log)                                                                      │ included                    │                     │
├─────┼──────────────┼───────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────┼─────────────────────┤
│ 71  │ RTWC2        │ Not available to any civ                                                          │ —                           │ —                   │
└─────┴──────────────┴───────────────────────────────────────────────────────────────────────────────────┴─────────────────────────────┴─────────────────────┘

- Cuman override: the 2023 civ help text says "Additional Town Center can be built in the Feudal Age", so a new data/manual/manual_civ_overrides.csv sets the Cumans to age 2 with that text as evidence. The 2026 tree agrees.
- Plausibility: the README now says the check must use the per-civ age in civilization_object.
- TC builds before the estimated Castle Age (Castle Age command + 160s):

| Sessions           | TC builds | Before Castle Age |
|--------------------|-----------|-------------------|
| No starting TC     | 255       | 115 (45.1%)       |
| With a starting TC | 3,188     | 208 (6.5%)        |
  The first group is expected, since those maps start without a TC. Phase 4 can show how many of the 208 are Cumans.

2. Population: neither data version has pop cost or housing, so I didn't add the columns. It's listed as a limitation.

3. Civ groups: civ_group and civ_group_member are created now (still empty), so session.civ_group_id is already a real foreign key. Phase 4's "never regenerate the label" rule will need the labels stored outside the database, since 03 resets it.

4. scripts/run_all.py: runs 03, then 04, and later steps once they exist, and stops on the first error. The README warns that running 03 alone wipes the replay data.

5. Limitations added to the README:
- No civ bonuses on costs or times.
- Mule Cart, Krepost and Donjon aren't modelled as age-up buildings.
- The Folwark and Fortified Church counting toward age-ups is an assumption.
- The Xolotl Warrior tree quirk.
- No population data.

Phase 3 report

Row counts and source check:

┌───────────────────┬────────────┬──────────────────────────────────────────┐
│       Table       │    Rows    │           Matches the source?            │
├───────────────────┼────────────┼──────────────────────────────────────────┤
│ match             │ 1,000      │ yes                                      │
├───────────────────┼────────────┼──────────────────────────────────────────┤
│ player            │ 1,759      │ yes                                      │
├───────────────────┼────────────┼──────────────────────────────────────────┤
│ session           │ 2,000      │ yes                                      │
├───────────────────┼────────────┼──────────────────────────────────────────┤
│ event             │ 2,372,505  │ yes, and 0 of the 829 event types differ │
├───────────────────┼────────────┼──────────────────────────────────────────┤
│ event_object_link │ 10,587,042 │ yes                                      │
└───────────────────┴────────────┴──────────────────────────────────────────┘

Timestamps are game time. Villager Start → Complete is 25s in all 203,373 cases, which equals the 2023 train time. In real time at 1.7× speed it would be about 15s. The median match is 35 minutes.

Share of events with a game object:
- 72.7% of all events. Gather and Set Gather Point carry a resource instead of an object.
- 100% of Queue, Research and Build events, of which 147 Command or Start Build events point to UNKNOWN.

Vocabulary: 265 game objects across 615,296 events.

┌──────────┬─────────┬─────────┐
│  Action  │ Objects │ Events  │
├──────────┼─────────┼─────────┤
│ Queue    │ 72      │ 442,497 │
├──────────┼─────────┼─────────┤
│ Research │ 168     │ 20,963  │
├──────────┼─────────┼─────────┤
│ Build    │ 25      │ 151,836 │
└──────────┴─────────┴─────────┘

The README's 269 action types become 265 because the 4 placeholder types are excluded.

Session flags:

┌─────────────────────┬──────────────────────────────────────────────────────────────┬──────────┐
│        Flag         │                          Definition                          │ Sessions │
├─────────────────────┼──────────────────────────────────────────────────────────────┼──────────┤
│ short_match         │ Match spans under 300s                                       │ 110      │
├─────────────────────┼──────────────────────────────────────────────────────────────┼──────────┤
│ no_commands         │ No Command events                                            │ 70       │
├─────────────────────┼──────────────────────────────────────────────────────────────┼──────────┤
│ outlier_commands    │ More Command events than the 99th percentile, which is 1,204 │ 19       │
├─────────────────────┼──────────────────────────────────────────────────────────────┼──────────┤
│ has_unmapped_events │ Has a Queue, Research or Build event mapped to UNKNOWN       │ 79       │
├─────────────────────┼──────────────────────────────────────────────────────────────┼──────────┤
│ Any flag            │                                                              │ 212      │
└─────────────────────┴──────────────────────────────────────────────────────────────┴──────────┘

Sample session: S268755395_1501487 is the unflagged session with the median number of Commands. The report shows its decisions for the first 15 minutes, with all 293 events in a folded table. It starts with 4 villagers and an Eagle Scout, which points to Mayans. The opening is Loom, 2 houses, villagers, then Lumber Camp, Mill, Feudal Age at 06:44, Barracks, Archery Range and archers and spearmen around 10:10.

How I filled event.object_ref (all raw links are also kept in event_object_link):
- Build and starting objects: the object that was created.
- Gather: the villager.
- Queue, Research and Set Gather Point: the producing building, which can be a UO ID.

Files

- New: scripts/04_load_replays.py, scripts/run_all.py, data/manual/manual_civ_overrides.csv, reports/replay_load.md.
- Changed:
  - 02_schema.sql
  - 03_load_static.py now supports a target_tree_id column and the civ overrides.
  - manual_mapping.csv has a target_tree_id column and the two Town Center rows.
  - The README.

Nothing is committed.
