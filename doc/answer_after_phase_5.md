Phase 4.1 and Phase 5 are done and I've stopped before Phase 6. Your replacement-building hypothesis was right for the Folwark and the Fortified Church, and with that fix every session now has at least one candidate civ. The plausibility checker flags 0.20% of real player decisions, against 76% of random substitutes, so it's both quiet and useful.

Phase 4.1: replacement buildings

1. Which civs lack the base buildings.

┌──────────────────────────┬──────────────────────┬──────────────────┐
│      Base building       │   Civs without it    │   Replaced by    │
├──────────────────────────┼──────────────────────┼──────────────────┤
│ Lumber Camp, Mining Camp │ Armenians, Georgians │ Mule Cart        │
├──────────────────────────┼──────────────────────┼──────────────────┤
│ Mill                     │ Poles                │ Folwark          │
├──────────────────────────┼──────────────────────┼──────────────────┤
│ Monastery                │ Armenians, Georgians │ Fortified Church │
├──────────────────────────┼──────────────────────┼──────────────────┤
│ Castle                   │ none                 │ —                │
└──────────────────────────┴──────────────────────┴──────────────────┘

The names Folwark, Fortified Church and Mule Cart never appear in the log.

2. What the log does with them.
- Folwark → "Mill": confirmed. Both sessions that train the Poles-only Obuch log "Mill" builds (7 and 5).
- Fortified Church → "Monastery": confirmed. Both sessions that train the Armenian Warrior Priest log "Monastery" builds (7 and 1).
- Mule Cart: not supported. Those same two Armenian sessions log no Lumber or Mining Camp at all, so I left Mule Cart out of the equivalences.

The two confirmed cases are in data/manual/replacement_equivalents.csv. Civ inference and is_plausible now treat a logged "Mill" or "Monastery" as base or replacement.

3. Effect on civ inference.

┌───────────────┬─────────────┬────────────────────┐
│ Possible civs │   Before    │       After        │
├───────────────┼─────────────┼────────────────────┤
│ 0             │ 4           │ 0 (all 4 resolved) │
├───────────────┼─────────────┼────────────────────┤
│ 1             │ 582 (29.1%) │ 585 (29.2%)        │
├───────────────┼─────────────┼────────────────────┤
│ 2–10          │ 260         │ 257                │
├───────────────┼─────────────┼────────────────────┤
│ 11 or more    │ 1,154       │ 1,158              │
└───────────────┴─────────────┴────────────────────┘

- Newly identified: Poles 0 → 2 sessions, Armenians 0 → 2. Georgians still never narrow to one civ.
- Group changes: 949 sessions changed group.
- Labels: 299 new labels were appended. No existing row changed; the script re-reads the file and verifies that. 300 old labels now have no session and stay in the file as they are. No key changed meaning; keys are civ sets, and the script checks that the civ IDs haven't changed.

4. Age-gated objects before the age command.
- Overall: 409 of 1,969 sessions with decisions (20.8%) use an age-gated object before, or entirely without, the matching age command.
- Strict version (before the command plus the research time): 493 (25.0%).
- Most common objects: Town Center, Trebuchet, Blacksmith, Market.
- The TC sessions: 32 have an unexplained early TC build, slightly more than the earlier 31 because candidate sets shifted. 31 of the 32 use other Castle-Age objects (Monastery, Castle, University, Monk), so they did reach Castle Age and the age command is missing from the log.

Phase 5: resources and plausibility

Run: 06_resources.py takes about 15s, and all 4 scripts together (run_all.py) take about 80s. The options are --deduction-phase, --start, --max-gather-rate and --no-civ-start-adjustments, and the settings used are stored in the setting table.

Phase 5: resources and plausibility

Run: 06_resources.py takes about 15s, and all 4 scripts together (run_all.py) take about 80s. The options are --deduction-phase, --start, --max-gather-rate and --no-civ-start-adjustments, and the settings used are stored in the setting table.

Tables (all marked as estimated):

┌───────────────────┬─────────┬────────────────────────────────────────────────────────────────────────────────────────────────────┐
│       Table       │  Rows   │                                              Content                                               │
├───────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ session_start     │ 2,000   │ Starting resources and villagers                                                                   │
├───────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ session_age       │ 6,130   │ When each age was reached, and from which evidence                                                 │
├───────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ resource_snapshot │ 615,296 │ Cumulative spending, minimum gathered, villagers so far and current age, at every vocabulary event │
├───────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ setting           │ 6       │ The settings 06 ran with                                                                           │
└───────────────────┴─────────┴────────────────────────────────────────────────────────────────────────────────────────────────────┘

Starting resources: 200 food, 200 wood, 100 gold, 200 stone. They're adjusted only for sessions narrowed to one civ, using a new data/manual/start_resources.csv that quotes the 2023 help texts:

┌─────────────┬───────────────────┐
│     Civ     │ Sessions adjusted │
├─────────────┼───────────────────┤
│ Mayans      │ 44                │
├─────────────┼───────────────────┤
│ Chinese     │ 23                │
├─────────────┼───────────────────┤
│ Lithuanians │ 18                │
├─────────────┼───────────────────┤
│ Persians    │ 13                │
├─────────────┼───────────────────┤
│ Aztecs      │ 11                │
├─────────────┼───────────────────┤
│ Sicilians   │ 5                 │
├─────────────┼───────────────────┤
│ Huns        │ 2                 │
└─────────────┴───────────────────┘

Current age = the earlier of (age command + research time) and (first use of an object that needs the age):

┌──────────┬──────────┬──────────────────┬────────────────┬───────────────────────────┬─────────────────────┐
│   Age    │ Sessions │ From the command │ From an object │ No age command in the log │ Median time reached │
├──────────┼──────────┼──────────────────┼────────────────┼───────────────────────────┼─────────────────────┤
│ Feudal   │ 1,844    │ 1,612            │ 232            │ 97                        │ 9.5 min             │
├──────────┼──────────┼──────────────────┼────────────────┼───────────────────────────┼─────────────────────┤
│ Castle   │ 1,570    │ 1,344            │ 226            │ 93                        │ 21.2 min            │
├──────────┼──────────┼──────────────────┼────────────────┼───────────────────────────┼─────────────────────┤
│ Imperial │ 716      │ 538              │ 178            │ 162                       │ 37.4 min            │
└──────────┴──────────┴──────────────────┴────────────────┴───────────────────────────┴─────────────────────┘

is_plausible(session_id, time_s, game_object_id) returns (ok, [(reason, detail), …]). It lives in scripts/aoe2_plausibility.py, because a script whose name starts with a digit can't be imported. Only evidence from before time_s counts. In the sample session, Crossbowman at 1 min fails on age and producer, at 10 min on age, and passes at 25 min.

Validation on all 615,296 real decisions:

┌──────────────┬─────────────────────────────────────┬────────────────────────────┐
│    Reason    │       Real decisions flagged        │ Random substitutes flagged │
├──────────────┼─────────────────────────────────────┼────────────────────────────┤
│ Any reason   │ 0.20% (0.16% in unflagged sessions) │ 75.8%                      │
├──────────────┼─────────────────────────────────────┼────────────────────────────┤
│ Civ          │ 0.00%                               │ 40.2%                      │
├──────────────┼─────────────────────────────────────┼────────────────────────────┤
│ Age          │ 0.11%                               │ 23.9%                      │
├──────────────┼─────────────────────────────────────┼────────────────────────────┤
│ Prerequisite │ 0.03%                               │ 9.7%                       │
├──────────────┼─────────────────────────────────────┼────────────────────────────┤
│ Producer     │ 0.00%                               │ 26.2%                      │
├──────────────┼─────────────────────────────────────┼────────────────────────────┤
│ Spending     │ 0.06%                               │ 1.4%                       │
└──────────────┴─────────────────────────────────────┴────────────────────────────┘

The random-substitute column is a control I added: each real decision swapped for a random object at the same session and time. It shows the checker actually catches implausible actions.

Top 10 false alarms and their causes:
- Age: Knight (196), Trebuchet (96), Camel Rider (53), Stable (48), Town Center (30), Archery Range (28). These are first uses in sessions where the age command is missing from the log.
- Spending: Scout Cavalry (163), Hand Cannoneer (143), Villager (28). 332 of all 361 spending alarms come from one outlier session.
- Prerequisite: Castle Age (57), flagged because the Feudal Age command is missing.

Spending check

The spending check needs some upper limit on income. I assumed at most 1 resource per villager-second, which is well above real gather rates but still a number I chose rather than one from the data. It's configurable with --max-gather-rate and documented as a bound, not an estimate. Without some cap the spending check could never fail.

I also exempted the first Town Center on maps without a starting TC from the age check. It removed 98 false alarms.

Files

- New:
  - scripts/06_resources.py and scripts/aoe2_plausibility.py.
  - data/manual/replacement_equivalents.csv and data/manual/start_resources.csv.
  - reports/resources.md.
- Changed:
  - 02_schema.sql, 05_civ_inference.py and the README (decisions and limitations, including the missing age commands and overestimated spending).
  - reports/civ_inference.md now has the Phase 4.1 sections.
  - data/manual/civ_group_labels.csv has the appended rows.

Nothing is committed.