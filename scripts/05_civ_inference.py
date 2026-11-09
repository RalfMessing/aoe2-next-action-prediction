#!/usr/bin/env python3
"""Phase 4: narrow down each session's possible civilizations.

Evidence per session:
  a) every in-vocabulary game object the player used: candidates are the civs whose
     civilization_object contains it (availability only; ages are ignored);
  b) start signals from data/manual/start_signals.csv (starting objects, villager count),
     used only when the opponent in the same match does not show the same signal.
Candidates = intersection of all evidence. An empty set goes to the special group 'none',
and session.civ_conflict records the evidence that emptied it (evidence is applied from
the rarest to the most common).

Group labels live in data/manual/civ_group_labels.csv (key = sorted civ IDs). Existing
rows are never changed; new keys are appended with a generated label. Group ids follow
the row order of that file, so they stay stable.

Writes reports/civ_inference.md.
Usage: python3 scripts/05_civ_inference.py [--db PATH]
"""
import argparse
import collections
import csv
import datetime as dt
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data/processed/aoe2.db"
LABELS = ROOT / "data/manual/civ_group_labels.csv"
SIGNALS = ROOT / "data/manual/start_signals.csv"
EQUIVALENTS = ROOT / "data/manual/replacement_equivalents.csv"
REPORT = ROOT / "reports/civ_inference.md"
LABEL_FIELDS = ["key", "civs", "label", "description", "created"]
NONE_KEY = "none"
BROAD_ABOVE = 10
TYPE_ORDER = {"UNIT": 0, "TECHNOLOGY": 1, "BUILDING": 2}
# Civs whose age-ups finish faster than the generic research time (2023 civ help texts).
# Value: speed factor for the Castle Age research.
FAST_AGE_UP = {
    "Malay": (1.66, "'Advancing to the next age 66% faster'"),
    "Persians": (1.10, "'Town Center ... work rate +10% Feudal'"),
}
CASTLE_AGE_TIME_FALLBACK = 160


def md_table(headers, rows):
    def cell(value):
        return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def bucket(size):
    return "0" if size == 0 else "1" if size == 1 else "2-10" if size <= 10 else "11+"


class Inference:
    def __init__(self, con):
        self.con = con
        q = con.execute
        self.civ_name = dict(q("SELECT id, name FROM civilization"))
        self.civ_id = {n: i for i, n in self.civ_name.items()}
        self.all_civs = frozenset(self.civ_name)
        self.objects = {i: (t, n, u) for i, t, n, u in q("SELECT id, type, name, upgrades_to FROM game_object")}
        self.available = collections.defaultdict(set)
        for civ, obj in q("SELECT civ_id, object_id FROM civilization_object"):
            self.available[obj].add(civ)
        self.available = {k: frozenset(v) for k, v in self.available.items()}
        self.session_ref = dict(q("SELECT id, source_id FROM session"))
        self.session_match = dict(q("SELECT id, match_id FROM session"))
        # Logged base name -> also evidence for the replacement building (Mill -> Folwark, ...)
        self.equivalents = {}
        by_name = {(t, n): i for i, (t, n, _) in self.objects.items()}
        with open(EQUIVALENTS, newline="") as handle:
            for row in csv.DictReader(handle):
                base = by_name[(row["base_type"], row["base_name"])]
                replacement = by_name[(row["replacement_type"], row["replacement_name"])]
                self.equivalents[base] = (replacement, row["replacement_name"])

    def civs_for(self, obj, use_equivalents=True):
        civs = self.available.get(obj, frozenset())
        if use_equivalents and obj in self.equivalents:
            civs = civs | self.available.get(self.equivalents[obj][0], frozenset())
        return civs

    # -- evidence -----------------------------------------------------------------------------
    def object_evidence(self, use_equivalents=True):
        evidence = collections.defaultdict(list)
        for session, obj in self.con.execute(
                "SELECT DISTINCT session_id, game_object_id FROM event WHERE in_vocabulary = 1 ORDER BY 1, 2"):
            evidence[session].append((self.objects[obj][1], self.civs_for(obj, use_equivalents), obj))
        return evidence

    def start_signal_evidence(self):
        signals = []
        with open(SIGNALS, newline="") as handle:
            for row in csv.DictReader(handle):
                civs = frozenset(self.civ_id[c] for c in row["civs"].split(";"))
                signals.append((row["signal_type"], row["value"], civs))
        start = collections.defaultdict(collections.Counter)
        for session, ocel_type, n in self.con.execute(
                "SELECT session_id, ocel_type, COUNT(*) FROM event WHERE action='CREATE_INITIAL' GROUP BY 1, 2"):
            start[session][ocel_type[len("Create Initial "):]] = n
        by_match = collections.defaultdict(list)
        for session, match in self.session_match.items():
            by_match[match].append(session)
        evidence = collections.defaultdict(list)
        ignored = collections.Counter()
        for session, match in self.session_match.items():
            opponents = [s for s in by_match[match] if s != session]
            for kind, value, civs in signals:
                def shows(s):
                    if kind == "villagers":
                        return start[s]["Villager"] == int(value)
                    return start[s][value] > 0
                if not shows(session):
                    continue
                label = f"start: {value} villagers" if kind == "villagers" else f"start: {value}"
                if any(shows(o) for o in opponents):
                    ignored[label] += 1
                    continue
                evidence[session].append((label, civs, None))
        return evidence, ignored

    @staticmethod
    def narrow(items, all_civs):
        """Intersect from the rarest evidence up. Returns (candidates, emptying item or None)."""
        candidates = set(all_civs)
        for item in sorted(items, key=lambda e: (len(e[1]), e[0])):
            if not candidates & item[1]:
                return frozenset(), item
            candidates &= item[1]
        return frozenset(candidates), None

    # -- labels -------------------------------------------------------------------------------
    def describe(self, key, candidates, narrowing):
        if key == NONE_KEY:
            return ("none (no civ fits)",
                    "The evidence contradicts every civ. session.civ_conflict names the evidence that emptied the set.")
        names = sorted(self.civ_name[c] for c in candidates)
        evidence = ", ".join(f"{n} ({c} sessions)" for n, c in narrowing.most_common(3))
        if len(candidates) == 1:
            return names[0], f"Narrowed by: {evidence}."
        if len(candidates) > BROAD_ABOVE:
            others = sorted(self.civ_name[c] for c in self.all_civs - candidates)
            if not others:
                excluded = "All civs."
            elif len(others) <= 15:
                excluded = f"All civs except {', '.join(others)}."
            else:
                excluded = f"Candidates: {', '.join(names)}."
            return f"broad ({len(candidates)} civs)", f"{excluded} Narrowed by: {evidence or 'nothing'}."
        shared = sorted((o for o, civs in self.available.items()
                         if candidates <= civs and self.objects[o][2] is None and self.objects[o][0] in TYPE_ORDER),
                        key=lambda o: (len(self.available[o]), TYPE_ORDER[self.objects[o][0]], self.objects[o][1]))
        missing = sorted((o for o, civs in self.available.items()
                          if not candidates & civs and self.objects[o][2] is None and self.objects[o][0] in TYPE_ORDER),
                         key=lambda o: (-len(self.available[o]), TYPE_ORDER[self.objects[o][0]], self.objects[o][1]))
        label = self.small_label(candidates, names, shared, missing)
        parts = [f"Candidates: {', '.join(names)}."]
        if shared:
            parts.append("Rarest shared: " + ", ".join(
                f"{self.objects[o][1]} ({len(self.available[o])} civs)" for o in shared[:3]) + ".")
        if missing:
            parts.append("Common but missing: " + ", ".join(
                f"{self.objects[o][1]} ({len(self.available[o])} civs)" for o in missing[:3]) + ".")
        parts.append(f"Narrowed by: {evidence}.")
        return label, " ".join(parts)

    def small_label(self, candidates, names, shared, missing):
        """Readable label for 2-10 candidates, in order of preference."""
        if len(candidates) <= 3:
            return " / ".join(names)
        name = lambda o: self.objects[o][1]
        for o in shared:  # an object exactly these civs have
            if self.available[o] == candidates:
                return f"{name(o)} civs"
        civs, terms = set(self.all_civs), []  # up to 3 shared objects whose civ sets intersect exactly
        while civs != candidates and len(terms) < 3:
            o = min(shared, key=lambda o: (len(civs & self.available[o]), shared.index(o)))
            if civs & self.available[o] == civs:
                break
            terms.append(o)
            civs &= self.available[o]
        if civs == candidates:
            return " + ".join(name(o) for o in terms) + " civs"
        best = None  # shared object minus up to 3 missing objects, chosen greedily
        for o in shared[:8]:
            extra, terms = set(self.available[o] - candidates), []
            while extra and len(terms) < 3:
                m = max(missing, key=lambda m: (len(extra & self.available[m]), -missing.index(m)), default=None)
                if m is None or not extra & self.available[m]:
                    break
                terms.append(m)
                extra -= self.available[m]
            if not extra and (best is None or len(terms) < len(best[1])):
                best = (o, terms)
        if best:
            return f"{name(best[0])} civs without {', '.join(name(m) for m in best[1])}"
        for o in shared:  # shared object, a few civs fewer
            extra = sorted(self.civ_name[c] for c in self.available[o] - candidates)
            if len(extra) <= 3:
                return f"{name(o)} civs except {', '.join(extra)}"
        return f"{len(candidates)} civs with {name(shared[0])}" if shared else f"{len(candidates)} civs"

    def key_of(self, candidates):
        return ",".join(str(c) for c in sorted(candidates)) if candidates else NONE_KEY

    def civs_text(self, key):
        return "" if key == NONE_KEY else ";".join(self.civ_name[int(c)] for c in key.split(","))


def load_labels(inference):
    rows = []
    if LABELS.exists():
        with open(LABELS, newline="") as handle:
            rows = list(csv.DictReader(handle))
    for row in rows:
        if row["key"] != NONE_KEY and inference.civs_text(row["key"]) != row["civs"]:
            sys.exit(f"civ_group_labels.csv: key {row['key']} no longer means {row['civs']}. "
                     "The civilization ids changed; fix the file before continuing.")
    return rows


def main():
    parser = argparse.ArgumentParser(description="Narrow down each session's possible civilizations.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    started = time.time()
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON")
    if con.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 0:
        sys.exit("session is empty. Run scripts/04_load_replays.py first.")
    inf = Inference(con)

    objects = inf.object_evidence()
    objects_no_equivalents = inf.object_evidence(use_equivalents=False)
    signals, ignored_signals = inf.start_signal_evidence()
    result, result_plain, result_before = {}, {}, {}
    for session in sorted(inf.session_ref):
        result_plain[session] = inf.narrow(objects[session], inf.all_civs)
        result[session] = inf.narrow(objects[session] + signals[session], inf.all_civs)
        result_before[session] = inf.narrow(objects_no_equivalents[session] + signals[session], inf.all_civs)

    # Labels: keep existing rows, append new keys
    rows = load_labels(inf)
    existing_rows = [dict(r) for r in rows]
    known = {r["key"] for r in rows}
    used_labels = {r["label"] for r in rows}
    by_key = collections.defaultdict(list)
    for session, (candidates, _) in result.items():
        by_key[inf.key_of(candidates)].append(session)
    new_rows = []
    for key in sorted(set(by_key) - known):
        narrowing = collections.Counter()
        for session in by_key[key]:
            items = objects[session] + signals[session]
            rarest = min(items, key=lambda e: (len(e[1]), e[0])) if items else None
            if rarest and len(rarest[1]) < len(inf.all_civs):
                narrowing[rarest[0]] += 1
        candidates = frozenset() if key == NONE_KEY else frozenset(int(c) for c in key.split(","))
        label, description = inf.describe(key, candidates, narrowing)
        base, n = label, 2
        # Readable labels of small groups are kept unique; "broad (N civs)" may repeat (the key differs).
        while label in used_labels and not label.startswith("broad ("):
            label, n = f"{base} #{n}", n + 1
        used_labels.add(label)
        new_rows.append({"key": key, "civs": inf.civs_text(key), "label": label, "description": description,
                         "created": dt.date.today().isoformat()})
    if new_rows:
        write_header = not LABELS.exists()
        with open(LABELS, "a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=LABEL_FIELDS, lineterminator="\n")
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
    rows += new_rows
    with open(LABELS, newline="") as handle:
        reread = list(csv.DictReader(handle))
    if reread[:len(existing_rows)] != existing_rows:
        sys.exit("civ_group_labels.csv: an existing row changed. This must never happen.")

    # Database: groups in file order, members, session assignment
    with con:
        con.execute("UPDATE session SET civ_group_id = NULL, civ_conflict = NULL, civ_conflict_object_id = NULL")
        con.execute("DELETE FROM civ_group_member")
        con.execute("DELETE FROM civ_group")
        group_id = {}
        for index, row in enumerate(rows, 1):
            con.execute("INSERT INTO civ_group (id, key, label, description) VALUES (?, ?, ?, ?)",
                        (index, row["key"], row["label"], row["description"]))
            group_id[row["key"]] = index
            if row["key"] != NONE_KEY:
                con.executemany("INSERT INTO civ_group_member VALUES (?, ?)",
                                [(index, int(c)) for c in row["key"].split(",")])
        con.executemany("UPDATE session SET civ_group_id = ?, civ_conflict = ?, civ_conflict_object_id = ? WHERE id = ?",
                        [(group_id[inf.key_of(c)], e[0] if e else None, e[2] if e else None, s)
                         for s, (c, e) in result.items()])
    if con.execute("PRAGMA foreign_key_check").fetchall():
        sys.exit("Foreign key violations")

    write_report(con, inf, result, result_plain, result_before, objects, signals, ignored_signals,
                 (len(existing_rows), len(new_rows)), time.time() - started)
    con.close()
    print(f"wrote {REPORT}; {len(new_rows)} new group label(s) appended to {LABELS.name} "
          f"in {time.time() - started:.0f}s")


def write_report(con, inf, result, result_plain, result_before, objects, signals, ignored_signals, label_counts, runtime):
    q = con.execute
    n = len(result)
    existing_labels, new_labels = label_counts
    out = ["# Civ inference (Phase 4)",
           f"Generated by `scripts/05_civ_inference.py` in {runtime:.0f} s. Rules: `civilization_object` (2023 tree), "
           f"`data/manual/start_signals.csv`, `data/manual/replacement_equivalents.csv`. Labels: "
           f"`data/manual/civ_group_labels.csv`: {existing_labels} existing rows kept unchanged (verified after "
           f"writing), {new_labels} appended this run."]

    out.append("## Replacement buildings logged under base names (Phase 4.1)")
    out.append("The log never uses the names Folwark, Fortified Church or Mule Cart. Sessions that train a unit only "
               "the replacement civ has show the base name instead: both Obuch (Poles) sessions log Mill builds, and both "
               "Warrior Priest (Armenians) sessions log Monastery builds. A logged base name therefore counts as evidence "
               "for *base or replacement*. Mule Cart is **not** treated as Lumber/Mining Camp: the two Warrior Priest "
               "sessions log no Lumber or Mining Camp at all, so there is no sign that Mule Carts appear under those names.")
    out.append(md_table(["Logged name", "Also means", "For civs"], [
        (inf.objects[b][1], name, ", ".join(sorted(inf.civ_name[c] for c in inf.available.get(r, ()))))
        for b, (r, name) in sorted(inf.equivalents.items())]))
    before = collections.Counter(bucket(len(c)) for c, _ in result_before.values())
    after = collections.Counter(bucket(len(c)) for c, _ in result.values())
    out.append(md_table(["Candidate civs", "Before (Phase 4)", "After (with equivalents)"],
                        [(b, f"{before[b]} ({pct(before[b], n)})", f"{after[b]} ({pct(after[b], n)})")
                         for b in ("0", "1", "2-10", "11+")]))
    rows = []
    for civ in ("Poles", "Armenians", "Georgians"):
        cid = inf.civ_id[civ]
        rows.append((civ, sum(1 for c, _ in result_before.values() if c == {cid}),
                     sum(1 for c, _ in result.values() if c == {cid}),
                     sum(1 for c, _ in result_before.values() if cid in c),
                     sum(1 for c, _ in result.values() if cid in c)))
    out.append(md_table(["Civ", "Identified alone, before", "after", "Among candidates, before", "after"], rows))
    moved = sum(1 for s in result if result[s][0] != result_before[s][0])
    unused = q("SELECT COUNT(*) FROM civ_group g WHERE NOT EXISTS (SELECT 1 FROM session s WHERE s.civ_group_id = g.id)").fetchone()[0]
    out.append(f"{moved} sessions changed group. {unused} groups in the label file now have no session; their rows "
               "stay in the file unchanged (keys are civ sets, so a key's meaning cannot change while the "
               "civilization ids stay the same, which 05 checks on every run).")
    out.append(age_gate_section(con, inf, result))

    out.append("## Group sizes")
    plain = collections.Counter(bucket(len(c)) for c, _ in result_plain.values())
    final = collections.Counter(bucket(len(c)) for c, _ in result.values())
    out.append(md_table(["Candidate civs", "Objects only", "Share", "Objects + start signals", "Share"],
                        [(b, plain[b], pct(plain[b], n), final[b], pct(final[b], n)) for b in ("0", "1", "2-10", "11+")]))
    used = collections.Counter(item[0] for items in signals.values() for item in items)
    out.append("Start signals used (opponent does not show the same signal) and ignored (both players show it, "
               "treated as map-dependent):\n\n" + md_table(
                   ["Signal", "Sessions used", "Sessions ignored"],
                   [(s, used[s], ignored_signals[s]) for s in sorted(set(used) | set(ignored_signals))]))
    changed = sum(1 for s in result if result[s][0] != result_plain[s][0])
    out.append(f"Start signals changed the candidate set of {changed} sessions.")
    conflicts = collections.Counter(e[0] for c, e in result.values() if e)
    if conflicts:
        out.append("Sessions with no candidate (group `none`), by the evidence that emptied the set (applied from "
                   "the rarest to the most common):\n\n" + md_table(["Evidence", "Sessions"], sorted(
                       conflicts.items(), key=lambda kv: (-kv[1], kv[0]))))

    out.append("## Top 15 groups")
    out.append(md_table(["Group", "Label", "Civs", "Sessions", "Description"], q("""
        SELECT g.id, g.label, (SELECT COUNT(*) FROM civ_group_member m WHERE m.group_id = g.id), COUNT(s.id),
               g.description
        FROM civ_group g JOIN session s ON s.civ_group_id = g.id GROUP BY g.id ORDER BY 4 DESC, 1 LIMIT 15""").fetchall()))
    out.append(f"{q('SELECT COUNT(DISTINCT civ_group_id) FROM session').fetchone()[0]} groups are in use.")

    out.append("## Sessions narrowed to exactly one civ")
    singles = collections.Counter(inf.civ_name[next(iter(c))] for c, _ in result.values() if len(c) == 1)
    expected = sum(singles.values()) / len(inf.civ_name)
    out.append(f"{sum(singles.values())} sessions ({pct(sum(singles.values()), n)}). With 2,000 sessions and 45 civs "
               f"a uniform spread would be about {n / 45:.0f} sessions per civ in total; this column only counts the "
               "identified ones.")
    out.append(md_table(["Civ", "Sessions"], sorted(((c, singles[c]) for c in inf.civ_id),
                                                   key=lambda kv: (-kv[1], kv[0]))))
    zero = sorted(c for c in inf.civ_id if singles[c] == 0)
    out.append(f"Civs never identified alone: {', '.join(zero) or 'none'}.")

    out.append("## Early Town Center builds")
    castle = q("SELECT time FROM game_object WHERE type='TECHNOLOGY' AND name='Castle Age'").fetchone()
    castle_time = castle[0] if castle else CASTLE_AGE_TIME_FALLBACK
    builds = q("""
        WITH age AS (SELECT e.session_id, MIN(e.time_s) AS cmd FROM event e JOIN game_object g ON g.id = e.game_object_id
                     WHERE e.phase='COMMAND' AND g.name='Castle Age' GROUP BY e.session_id),
             start_tc AS (SELECT DISTINCT session_id FROM event WHERE ocel_type='Create Initial Town Center')
        SELECT e.session_id, e.time_s, age.cmd FROM event e JOIN game_object g ON g.id = e.game_object_id
        JOIN start_tc st ON st.session_id = e.session_id LEFT JOIN age ON age.session_id = e.session_id
        WHERE e.action='BUILD' AND e.phase='START' AND g.name='Town Center'
          AND (age.cmd IS NULL OR e.time_s < age.cmd + ?)""", (castle_time,)).fetchall()
    cumans = inf.civ_id["Cumans"]
    categories = collections.Counter()
    sessions_by = collections.defaultdict(set)
    for session, t, cmd in builds:
        candidates = result[session][0]
        if not candidates:
            category = "no candidate civ (group none)"
        elif cumans in candidates:
            category = "Cumans possible (Feudal TC)" + (" — only Cumans" if candidates == {cumans} else "")
        elif cmd is not None and any(inf.civ_id[c] in candidates and t >= cmd + castle_time / f
                                     for c, (f, _) in FAST_AGE_UP.items()):
            category = "fast age-up civ possible (" + ", ".join(
                c for c, (f, _) in FAST_AGE_UP.items() if inf.civ_id[c] in candidates and t >= cmd + castle_time / f) + ")"
        elif cmd is None:
            category = "unexplained: no Castle Age command"
        elif t < cmd:
            category = "unexplained: before the Castle Age command"
        else:
            category = "unexplained: after the command, before Castle Age could complete"
        categories[category] += 1
        sessions_by[category].add(session)
    out.append(f"TC builds in sessions with a starting TC, before the estimated Castle Age (first Castle Age command "
               f"+ {castle_time} s): {len(builds)} builds in {len({b[0] for b in builds})} sessions. Fast age-up: "
               + "; ".join(f"{c} {e} (factor {f})" for c, (f, e) in FAST_AGE_UP.items()) + ".")
    out.append(md_table(["Explanation", "TC builds", "Sessions"],
                        [(c, k, len(sessions_by[c])) for c, k in sorted(categories.items(), key=lambda kv: (-kv[1], kv[0]))]))
    out.append("A build counts as explained when one of the session's candidate civs could legally build it; the civ "
               "is not confirmed by this.")

    out.append("## Matches where both players narrowed to one civ")
    by_match = collections.defaultdict(list)
    for session, (c, _) in result.items():
        by_match[inf.session_match[session]].append((session, c))
    both = sorted(m for m, ss in by_match.items() if len(ss) == 2 and all(len(c) == 1 for _, c in ss))
    rows = []
    for match in both[:12]:
        cells = []
        for session, c in sorted(by_match[match]):
            items = objects[session] + signals[session]
            key_evidence = sorted(items, key=lambda e: (len(e[1]), e[0]))[:2]
            cells.append(f"{inf.civ_name[next(iter(c))]} ({', '.join(e[0] for e in key_evidence)})")
        rows.append((q("SELECT source_id FROM match WHERE id=?", (match,)).fetchone()[0], *cells))
    out.append(f"{len(both)} matches. The first 12 by match id, with each session's two rarest pieces of evidence:")
    out.append(md_table(["Match", "Session 1", "Session 2"], rows))
    mirror = sum(1 for m in both if len({next(iter(c)) for _, c in by_match[m]}) == 1)
    out.append(f"Mirror matches (both players the same civ) among them: {mirror}.")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n\n".join(out) + "\n")


def age_gate_section(con, inf, result):
    """Sessions that use an age-gated object before (or without) the matching age command."""
    q = con.execute
    age_tech = {2: "Feudal Age", 3: "Castle Age", 4: "Imperial Age"}
    tech_ids = {age: q("SELECT id, time FROM game_object WHERE type='TECHNOLOGY' AND name=?", (name,)).fetchone()
                for age, name in age_tech.items()}
    ages = collections.defaultdict(dict)  # object -> civ -> age
    for civ, obj, age in q("SELECT civ_id, object_id, required_age FROM civilization_object"):
        ages[obj][civ] = age
    for base, (replacement, _) in inf.equivalents.items():
        for civ, age in ages[replacement].items():
            ages[base].setdefault(civ, age)
    command = collections.defaultdict(dict)
    for session, obj, t in q("SELECT session_id, game_object_id, MIN(time_s) FROM event WHERE phase='COMMAND' "
                             "AND game_object_id IN (?, ?, ?) GROUP BY 1, 2", tuple(v[0] for v in tech_ids.values())):
        age = next(a for a, v in tech_ids.items() if v[0] == obj)
        command[session][age] = t
    first_use = collections.defaultdict(dict)  # session -> obj -> first time
    for session, obj, t in q("SELECT session_id, game_object_id, MIN(time_s) FROM event WHERE in_vocabulary=1 GROUP BY 1, 2"):
        first_use[session][obj] = t
    sessions = {"before": set(), "strict": set()}
    by_age = collections.Counter()
    top = collections.Counter()
    for session, uses in first_use.items():
        candidates = result[session][0] or inf.all_civs
        for obj, t in uses.items():
            needed = min((a for c, a in ages[obj].items() if c in candidates), default=None)
            if needed is None or needed < 2:
                continue
            cmd = command[session].get(needed)
            if cmd is None or t < cmd:
                sessions["before"].add(session)
                by_age[(needed, "no command" if cmd is None else "before command")] += 1
                top[inf.objects[obj][1]] += 1
            if cmd is None or t < cmd + tech_ids[needed][1]:
                sessions["strict"].add(session)
    out = ["## Age-gated objects before the age command (Phase 4.1)",
           "For each session and object, the first use is compared with the first command of the age the object "
           "needs (the earliest age among the session's candidate civs). 'Strict' also adds the age's research time."]
    with_cmds = len(first_use)
    out.append(f"Sessions using an age-gated object before or without the age command: **{len(sessions['before'])}** of "
               f"{with_cmds} sessions with decisions ({pct(len(sessions['before']), with_cmds)}). Strict (before command + "
               f"research time): {len(sessions['strict'])} ({pct(len(sessions['strict']), with_cmds)}).")
    out.append(md_table(["Needed age", "Case", "Objects (first uses)"],
                        [(f"{a} ({age_tech[a]})", case, k) for (a, case), k in sorted(by_age.items())]))
    out.append("Most frequent objects:\n\n" + md_table(["Object", "First uses before/without the command"],
                                                      top.most_common(10)))
    tc_sessions = [r[0] for r in q("""
        WITH start_tc AS (SELECT DISTINCT session_id FROM event WHERE ocel_type='Create Initial Town Center'),
             castle AS (SELECT DISTINCT session_id FROM event e JOIN game_object g ON g.id=e.game_object_id
                        WHERE e.phase='COMMAND' AND g.name='Castle Age')
        SELECT DISTINCT e.session_id FROM event e JOIN game_object g ON g.id = e.game_object_id
        WHERE e.action='BUILD' AND e.phase='START' AND g.name='Town Center'
          AND e.session_id IN (SELECT session_id FROM start_tc) AND e.session_id NOT IN (SELECT session_id FROM castle)""")]
    cumans = inf.civ_id["Cumans"]
    unexplained = [x for x in tc_sessions if result[x][0] and cumans not in result[x][0]]
    castle_users = collections.Counter()
    using = 0
    for session in unexplained:
        candidates = result[session][0] or inf.all_civs
        gated = [inf.objects[o][1] for o in first_use[session]
                 if inf.objects[o][1] != "Town Center"
                 and min((a for c, a in ages[o].items() if c in candidates), default=0) >= 3]
        using += bool(gated)
        castle_users.update(set(gated))
    out.append(f"{len(tc_sessions)} sessions with a starting TC build a TC but never command Castle Age; "
               f"{len(unexplained)} of them cannot be Cumans (these are the unexplained ones from the TC table below). "
               f"{using} of these {len(unexplained)} use other "
               "Castle-Age-gated objects (so they did reach Castle Age; the age command is missing from the log). "
               "Most frequent: " + ", ".join(f"{o} ({k})" for o, k in castle_users.most_common(8)) + ".")
    return "\n\n".join(out)


if __name__ == "__main__":
    main()
