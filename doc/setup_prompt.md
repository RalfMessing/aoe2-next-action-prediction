# Task: Build a cleaned AoE2 database from OCEL replays + aoe2techtree data

## Context
University project (AI & Visualization): next-action prediction on Age of Empires II: DE build orders.
We need ONE clean SQLite database that combines:
1. **Replay events**: OCEL 2.0 SQLite from Zenodo ("Object-Centric Event Log for Age of Empires Game Interactions", 1,000 matches from 2023, ~2.4M events, ~829 event types, ~30 object types). Path: `<PATH_TO_OCEL_SQLITE>`
2. **Game rules**: `data.json` from aoe2techtree.net (costs, train times, per-civ lists of available buildings/techs/units as numeric IDs). Path: `<PATH_TO_DATA_JSON>`

Known facts (verify, don't trust blindly):
- OCEL 2.0 SQLite normally has tables `event`, `object`, `event_object` (E2O with qualifier), `object_object` (O2O), `event_map_type`, `object_map_type`, plus one table per event/object type.
- Object types include Match, Player, Session (= one player in one match), Villager, Town Center, buildings, units. Player-input events link to Player; engine events link only to Session.
- Event types follow patterns like "Command Build …", "Start Build …", "Complete Build …", "Command Research …", "Start Research …", "Complete Research …", "Command Queue …", "Start Production …", "Complete Queue …", "Gather …". There are many more types, so find all of them.
- Civ, map, Elo and winner attributes are reportedly empty in the replays.
- `data.json` has `civs` (per civ: `Building`, `Tech`, `Unit` ID lists) and entries with `Cost`, `TrainTime`, `LanguageNameId`, `internal_name`. Readable names probably need the aoe2techtree string table (e.g. `data/locales/en/strings.json`). If you can't find it, tell me instead of guessing.

## Rules
- Never modify the source files. Write to a new file `aoe2.db` in data/processed.
- Python 3 + stdlib `sqlite3` (pandas only if really needed). Prefer `ATTACH` + `INSERT … SELECT` over Python loops for bulk data.
- All scripts must be idempotent (drop & rebuild) and runnable in order from the command line. A teammate will re-run them to verify.
- Don't invent data. Everything estimated must be flagged as estimated.

## Phase 1: Explore (STOP after this and report to me)
Write `01_explore.py` (in /scripts/, all scripts go there) that produces `reports/exploration.md` with:
- All tables + columns + row counts of the OCEL SQLite.
- All event types with counts, grouped by inferred pattern (action / phase / object name). Explicitly list types that fit NO known pattern.
- All object types with counts; sample rows of `event_object` and `object_object` incl. qualifiers.
- Which event/object attributes are actually filled.
- Structure of `data.json`: top-level keys, an example entry per category, whether research times and prerequisites exist.
- Name matching: distinct object names from the event types vs. names from `data.json`. Report the match rate and the list of unmatched names.
Then stop and summarize: surprises, unmatched names, and your recommendation for which additional event types we should persist.

## Phase 2: Schema + static data (after my OK)
`02_schema.sql` + `03_load_static.py`. Target schema:
- `game_object(id, name, internal_name, type[BUILDING|UNIT|TECHNOLOGY], required_age, time, cost_food, cost_wood, cost_gold, cost_stone, produced_at → game_object)`
- `dependency(object_id, requires_id)`: n:m prerequisites (if not in data.json, propose a source or a hand-maintained CSV for the ~100 build-order-relevant objects)
- `civilization(id, name)`
- `civilization_object(civ_id, object_id)`
- `name_mapping(ocel_name, game_object_id, method[exact|manual])`: plus a `manual_mapping.csv` for names that don't match automatically

## Phase 3: Replay data
`04_load_replays.py`:
- `match(id, duration, source_id)`, `player(id)`, `session(id, match_id, player_id)`
- `event(id, session_id, player_id NULL, action[BUILD|RESEARCH|QUEUE|GATHER|…], phase[COMMAND|START|COMPLETE|NULL], game_object_id NULL, resource NULL, time_s, ocel_type)`. Keep `ocel_type` raw so nothing is lost.
- Any additional event types we agreed on after Phase 1.

## Phase 4: Civ narrowing
`05_civ_inference.py`:
- Per session: set of game_objects the player used → candidate civs = civs whose `civilization_object` contains all of them.
- `civ_group(id, key, label, description)`, where `key` = sorted civ IDs (deterministic; same candidate set → same group, reused).
- `civ_group_member(group_id, civ_id)`, `session.civ_group_id`.
- Label: civ name if exactly one; otherwise a short human-readable label based on what the candidates share (e.g. a common unique unit/tech or the absence of a common tech). Store it, never regenerate per run.
- Report: distribution of group sizes, % of sessions with exactly one civ.

## Phase 5: Resource estimate
`06_resources.py` → `resource_snapshot(session_id, time_s, food, wood, gold, stone, villagers, estimated=1)`:
- Cumulative spending from cost × START events (make deduction phase configurable).
- Villager count over time from completed villager production.
- Starting resources configurable (default DE random map standard).
- Income is unknown: store cumulative spending and the implied *minimum* income needed. Don't fake gather rates.
- Purpose: later check whether a predicted action is plausible (affordable + prerequisites met). Add a function `is_plausible(session_id, time_s, game_object_id)` that checks prerequisites and the spending lower bound.

## Phase 6: Validation + docs
- `07_validate.py` → `reports/validation.md`: row counts source vs. target, % events mapped to a game_object, list of unmapped names, one full sample session printed as readable timeline.
- `README.md`: run order, runtime per script, known limitations.