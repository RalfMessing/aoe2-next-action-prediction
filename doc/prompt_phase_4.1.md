Phase 4 looks good, but check one suspected systematic error before Phase 5 (call it Phase 4.1):

Hypothesis: the log uses base building names for civ replacement buildings, e.g. a Polish Folwark logged as "Mill", an Armenian/Georgian Fortified Church as "Monastery", possibly Mule Cart as "Lumber Camp"/"Mining Camp". Evidence: Poles, Armenians and Georgians are never identified; the top broad groups exclude them via Lumber Camp; the 4 'none' sessions were emptied by Mill and Monastery.

1. Verify: for Lumber Camp, Mining Camp, Mill, Monastery, Castle, list which civs lack them in civilization_object and what replaces them. Check whether "Folwark", "Fortified Church", "Mule Cart" ever appear as names in the log.
2. If confirmed: add an equivalence table (data/manual/replacement_equivalents.csv: logged name → base object, replacement object, civs) and treat a logged base name as evidence for "base OR replacement" in civ inference. The 'none' sessions should then disappear or be explained.
3. Re-run and report: group sizes before/after, whether Poles/Armenians/Georgians now get identified, label CSV appended only (no existing label changed; if a key's civ set changes meaning, report it instead of overwriting).
4. The 31 sessions with TC builds but no Castle Age command: check whether they use Castle-Age-gated objects. Report how many sessions overall use an age-gated object before (or without) the corresponding age command.

Then Phase 5 (06_resources.py), as specified earlier, plus:
- Current age per session over time = max(estimated age from age command + research time, age implied by first use of an age-gated object). Store which source was used.
- Starting resources: standard random map (200 food/200 wood/100 gold/200 stone), adjusted by start signals for identified civs only (e.g. Chinese, Mayans); configurable.
- resource_snapshot at every in_vocabulary event: cumulative spending per resource, villagers trained so far, current age, estimated=1.
- is_plausible(session_id, time_s, game_object_id) returns the reason(s) if implausible: civ (not available to any candidate civ), age, prerequisites (incl. groups), producer exists, spending lower bound.
- Validation: run is_plausible on every actual player decision. Real actions should almost always be plausible, so the violation rate is the checker's false-alarm rate. Report it per reason, with the top 10 violating objects.

Stop after Phase 5 with the report.