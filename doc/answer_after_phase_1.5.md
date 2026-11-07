The 2023 tree data fits the replays better, and I've stopped before Phase 2

The 2023 files contain every field the schema needs, and no log name needs a 2026 fallback.

The files are in data/raw/aoe2techtree_2023/, with the 2026 snapshot unchanged. The commit SHA is recorded in 00_fetch_techtree.py, in the folder's manifest.json and in the README. The full comparison is section 9 of reports/exploration.md; the report takes about 45s to rebuild and re-runs are identical.

1. Files at the 2023 commit

┌──────────────────────────────┬───────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│             File             │ Present?  │                                                What it provides                                                 │
├──────────────────────────────┼───────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ data/data.json               │ yes       │ Costs and train/research times; techtrees gives each civ's buildings, techs and units with an age each, plus    │
│                              │           │ its unique unit and techs                                                                                       │
├──────────────────────────────┼───────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ data/locales/en/strings.json │ yes       │ Display names; LanguageNameId resolves directly                                                                 │
├──────────────────────────────┼───────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ data/trees/<CIV>.json        │ no        │ These files didn't exist yet                                                                                    │
├──────────────────────────────┼───────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ js/techtree.js               │ yes,      │ The tree layout, which gives the producers and prerequisite links. I read it with regexes and don't run it.     │
│                              │ extra     │                                                                                                                 │
└──────────────────────────────┴───────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

All 391 objects that some civ has get a name, cost, time and per-civ age, and all 354 units and techs get a producer.

2. 2023 vs 2026

Civs: 2023 has 45 civs. The 11 that exist only in 2026 are exactly the ones I had assumed were newer than the replays: Danes, Jurchens, Khitans, Mapuche, Muisca, Saxons, Shu, Tupi, Varangians, Wei and Wu.

Specific objects:

┌─────────────┬─────────────────────┬──────────────────────────────┬───────────────────────┐
│    Name     │        2023         │             2026             │ Times used in the log │
├─────────────┼─────────────────────┼──────────────────────────────┼───────────────────────┤
│ Supplies    │ exists (33 civs)    │ missing                      │ 80                    │
├─────────────┼─────────────────────┼──────────────────────────────┼───────────────────────┤
│ Bearded Axe │ exists              │ missing                      │ 3                     │
├─────────────┼─────────────────────┼──────────────────────────────┼───────────────────────┤
│ Pavise      │ exists              │ missing                      │ 2                     │
├─────────────┼─────────────────────┼──────────────────────────────┼───────────────────────┤
│ Scutage     │ not under that name │ missing                      │ 2                     │
├─────────────┼─────────────────────┼──────────────────────────────┼───────────────────────┤
│ Hulk        │ missing             │ exists                       │ 0                     │
├─────────────┼─────────────────────┼──────────────────────────────┼───────────────────────┤
│ Trade Cog   │ exists              │ exists                       │ 0                     │
├─────────────┼─────────────────────┼──────────────────────────────┼───────────────────────┤
│ Trebuchet   │ exists (ID 331)     │ only as "Trebuchet (Packed)" │ 1,845                 │
└─────────────┴─────────────────────┴──────────────────────────────┴───────────────────────┘

- Scutage: in 2023 it's tech 757, renamed "Hauberk". The 2023 techtree.js still declares SCUTAGE = 757, so I'd put it in the manual mapping with that as the evidence.
- Hulk and Trade Cog: the log never queues either. Hulk didn't exist in 2023, and Trade Cog is simply unused in these matches.

Name matching against the log (counting player decisions):

┌──────────┬────────────────────┬────────────────────┬─────────────────────┬─────────────────────┐
│  Action  │ 2023 names matched │ 2026 names matched │ 2023 events covered │ 2026 events covered │
├──────────┼────────────────────┼────────────────────┼─────────────────────┼─────────────────────┤
│ Queue    │ 72 of 73           │ 71 of 73           │ 100%                │ 99.6%               │
├──────────┼────────────────────┼────────────────────┼─────────────────────┼─────────────────────┤
│ Research │ 166 of 170         │ 163 of 170         │ 99.6%               │ 99.2%               │
├──────────┼────────────────────┼────────────────────┼─────────────────────┼─────────────────────┤
│ Build    │ 25 of 26           │ 25 of 26           │ 100%                │ 100%                │
└──────────┴────────────────────┴────────────────────┴─────────────────────┴─────────────────────┘

With 2023, the only names left unmatched are the 4 None or empty placeholder types, Heavy Cav Archer (16 events) and Scutage.

The log's fixed durations against the rule files:

┌───────────────────────────────┬─────────────────┬─────────────────┐
│            Action             │ Match with 2023 │ Match with 2026 │
├───────────────────────────────┼─────────────────┼─────────────────┤
│ Units                         │ 38 of 72 (53%)  │ 30 of 71 (42%)  │
├───────────────────────────────┼─────────────────┼─────────────────┤
│ Research                      │ 21 of 166 (13%) │ 22 of 163 (13%) │
├───────────────────────────────┼─────────────────┼─────────────────┤
│ Buildings (longest time seen) │ 16 of 25        │ 16 of 25        │
└───────────────────────────────┴─────────────────┴─────────────────┘

Units match 2023 clearly better: Skirmisher 22s, Cavalry Archer 34s, Organ Gun 21s and Trebuchet 50s all match only 2023. Research durations stay largely wrong in both versions, and 88 research types still take 0s. So my Phase 1 conclusion stands: the log's Complete times are derived values and not measurements.

Camel Scout is now strongly supported as Camel Rider. In 2023 a Camel Scout trains in 48s and a Camel Rider in 22s, and the log's Camel Scout always takes 22s. In 2026 both units take 22s, which is why Phase 1 couldn't tell them apart.

Civ narrowing:

┌───────────────┬───────┬──────────────────────────┬───────────────┬──────────────────────────┐
│ Possible civs │ 2023  │ 2023, with camel mapping │ 2026, 45 civs │ 2026, with camel mapping │
├───────────────┼───────┼──────────────────────────┼───────────────┼──────────────────────────┤
│ 0             │ 9.8%  │ 0.2% (4 sessions)        │ 10.5%         │ 1.0%                     │
├───────────────┼───────┼──────────────────────────┼───────────────┼──────────────────────────┤
│ 1             │ 21.6% │ 23.5%                    │ 21.4%         │ 23.1%                    │
├───────────────┼───────┼──────────────────────────┼───────────────┼──────────────────────────┤
│ 2–10          │ 9.3%  │ 15.5%                    │ 9.1%          │ 16.2%                    │
├───────────────┼───────┼──────────────────────────┼───────────────┼──────────────────────────┤
│ 11 or more    │ 59.3% │ 60.8%                    │ 59.0%         │ 59.7%                    │
└───────────────┴───────┴──────────────────────────┴───────────────┴──────────────────────────┘

Between the two versions, objects the log uses differ in 81 values: costs, times, ages and which civs have them. Examples are Militia at 60 vs 50 food, Arson moving from Castle to Feudal Age, and Gillnets.

3. Proposed source rule for Phase 2

- Source per row: each game_object and civilization_object row gets source = 2023|2026, plus a source_detail naming the file a field came from.
- Expected result: all rows used by the log come out as 2023.
- Fallback: a 2026 row only appears for an object missing from 2023. The comparison found none among the log's names, so the 2026 tree would only be a cross-check.

Changes

- scripts/aoe2_rules.py: new shared loader that reads either version into the same shape; Phase 2 will reuse it.
- scripts/00_fetch_techtree.py: rewritten to also download the 2023 commit.
- scripts/01_explore.py: new section 9 with the comparison.
- .gitignore: one more line so data/raw/aoe2techtree_2023/ (1.4 MB) gets committed.
- README: the SHA in the Data section and step 0 in the run order.

Nothing is committed.

Decisions for you before Phase 2

1. Should Camel Scout map to Camel Rider unless the session started with a Camel Scout? I recommend yes, based on the 48s vs 22s evidence.
2. Can Scutage go into manual_mapping.csv as Hauberk (tech 757), and Heavy Cav Archer as Heavy Cavalry Archer?
3. Are the per-row source columns enough, or do you want a source per field?
4. Do you still want the open points from Phase 1 settled? That's a per-civ age and producer on civilization_object, and flagging suspicious sessions rather than deleting them.