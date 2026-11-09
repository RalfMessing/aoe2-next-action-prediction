Before answering your questions and starting with phase 2, do this first:
  Use aoe2techtree at commit 9d2f8987b2852695e997cc8ca4bee077108d1fbc (last commit of 2023, 2023-12-15) as the primary tree data.

  1. Fetch the data files at exactly that commit (e.g. via
     https://raw.githubusercontent.com/SiegeEngineers/aoe2techtree/9d2f8987b2852695e997cc8ca4bee077108d1fbc/data/...)
     into data/raw/aoe2techtree_2023/. Keep the 2026 snapshot where it is. Record the SHA in 00_fetch_techtree.py and the README.
  2. Report which files exist in the 2023 version (data.json, string table, per-civ tree files) and which are missing.
  3. Compare 2023 vs. 2026:
     - civ list (expect ~45 civs, no Jurchens/Khitans/Shu/Wei/Wu/Mapuche/Muisca/Tupi etc.)
     - whether Supplies, Bearded Axe, Pavise, Scutage exist; whether Hulk and Trade Cog are missing
     - name-matching coverage against the log (same table as in Phase 1)
     - how many of the log's fixed durations now match the train/research times
     - civ narrowing preview (same table as section 8)
  4. Rule: use 2023 files wherever they have the field; fall back to 2026 only for fields missing in 2023 (e.g. age/producer/prerequisites if there are no tree files). Store the source per field or per row (source = 2023|2026).
     Stop and report the comparison before continuing with Phase 2.

