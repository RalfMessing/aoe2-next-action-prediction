Phase 3 looks good. OK for Phase 4 (05_civ_inference.py).

Housekeeping:
- Make sure data/processed/ (aoe2.db) is in .gitignore.
- Civ group labels live in data/manual/civ_group_labels.csv (key = sorted civ IDs), loaded by 05 and only appended to for new keys. Never regenerate existing labels.

Civ inference:
1. Evidence per session:
   a) Every in_vocabulary game object the player used → candidate civs = civs whose civilization_object contains it (per-civ availability; ages don't matter here).
   b) Starting objects (CREATE_INITIAL) as extra evidence, kept in a hand-kept data/manual/start_signals.csv with evidence notes (e.g. Eagle Scout start → Meso civs; Camel Scout start → Gurjaras; villager count deviating from standard → specific civs). Only use a start signal if it differs from the opponent in the same match; if both players have it, treat it as map-dependent and ignore it.
2. Candidate set = intersection of a) and b). If it becomes empty: keep the session, assign a special group "none", and record the object that emptied the set (as in exploration 8.0).
3. civ_group: key = sorted civ IDs; label = civ name if size 1; readable label based on what the candidates share if size 2–10; "broad (N civs)" if > 10. Description: the distinguishing objects or signals.
4. Fill civ_group_member and session.civ_group_id.

Report (reports/civ_inference.md):
- Distribution of group sizes (0 / 1 / 2–10 / 11+), with and without start signals.
- Top 15 groups by session count with their labels.
- Civ distribution over sessions that narrowed to exactly 1 civ.
- The 208 early TC builds (sessions with starting TC): how many are compatible with Cumans, how many with fast-age-up civs (e.g. Malay), how many are still unexplained.
- Matches where both players narrowed to 1 civ: list a few as sanity check.

Stop after Phase 4 with the report.