#!/usr/bin/env python3
"""Phase 1: profile the OCEL log and the tech tree files, write reports/exploration.md.

Read-only: the OCEL database is opened with mode=ro, and all working tables are
temporary. The report is rebuilt from scratch on every run.

Usage:
    python3 scripts/00_fetch_techtree.py   # once, if data/raw/aoe2techtree/ is missing
    python3 scripts/01_explore.py [--ocel PATH] [--out PATH]

Pass the 10-match sample as --ocel for a quick trial run.
"""
import argparse
import collections
import difflib
import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aoe2_rules import (TECHTREE_2023_COMMIT, load_2023, load_2026,  # noqa: E402
                        normalize, parse_event_type)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OCEL = ROOT / "data/raw/age_of_empires_ocel2.sqlite"
DEFAULT_OUT = ROOT / "reports/exploration.md"
DATA_JSON = ROOT / "data/raw/data.json"
TECHTREE_DIR = ROOT / "data/raw/aoe2techtree"

# Patterns named in doc/setup_prompt.md, used to report what the spec did not foresee.
SPEC_PATTERN = re.compile(
    r"^(Command Build|Start Build|Complete Build|Command Research|Start Research|"
    r"Complete Research|Command Queue|Start Production|Complete Queue|Gather)( |$)"
)
SPEC_PREFIXES = ["Command Build", "Start Build", "Complete Build", "Command Research",
                 "Start Research", "Complete Research", "Command Queue", "Start Production",
                 "Complete Queue", "Gather"]
# Civs believed to have been released after the last replay (2023-12-14). This comes
# from outside the data. Section 8.3 lists the evidence the files can give for it.
ASSUMED_POST_REPLAY_CIVS = {"Danes", "Jurchens", "Khitans", "Mapuche", "Muisca", "Saxons",
                            "Shu", "Tupi", "Varangians", "Wei", "Wu"}
CAMEL_SCOUT_ID = 1755
CAMEL_RIDER_ID = 329
UNIT_NODE_TYPES = {"Unit", "UniqueUnit", "RegionalUnit"}
TECH_NODE_TYPES = {"Research", "UniqueTech", "RegionalTech", "UnitUpgrade"}
BUILDING_NODE_TYPES = {"BuildingTech", "BuildingNonTech", "UniqueBuilding", "RegionalBuilding"}
ACTION_NODE_TYPES = {"QUEUE": UNIT_NODE_TYPES, "RESEARCH": TECH_NODE_TYPES,
                     "BUILD": BUILDING_NODE_TYPES}
ACTION_CATEGORY = {"QUEUE": "Unit", "RESEARCH": "Tech", "BUILD": "Building"}


def show(name):
    return "(empty)" if name == "" else name


def md_table(headers, rows):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def fold(summary, body):
    return f"<details><summary>{summary}</summary>\n\n{body}\n\n</details>"


class Report:
    def __init__(self):
        self.parts = []

    def h(self, level, text):
        self.parts.append(f"{'#' * level} {text}")

    def p(self, text):
        self.parts.append(text)

    def render(self):
        return "\n\n".join(self.parts) + "\n"


def load_techtree():
    """Load data.json, the string table and all tree nodes, aggregated across civs."""
    data = json.loads(DATA_JSON.read_text())
    strings_path = TECHTREE_DIR / "strings_en.json"
    if not strings_path.exists():
        sys.exit(f"Missing {strings_path}. Run scripts/00_fetch_techtree.py first.")
    strings = json.loads(strings_path.read_text())
    nodes = {}  # (category, numeric id) -> aggregated node
    civ_available = {}  # civ -> category -> ids that the tree does not mark NotAvailable
    for civ in data["civs"]:
        tree = json.loads((TECHTREE_DIR / f"trees/{civ.upper()}.json").read_text())
        available = {"Building": set(), "Tech": set(), "Unit": set()}
        for raw in tree["buildings"] + tree["units_techs"]:
            category, numeric, _ = raw["id"].split("_")
            key = (category, int(numeric))
            node = nodes.setdefault(key, {
                "category": category, "id": int(numeric), "name": raw["name"],
                "names": set(), "string_ids": set(), "node_types": set(), "ages": set(),
                "producers": set(), "links": set(), "civs_available": set(),
                "internal_name": None,
            })
            node["names"].add(raw["name"])
            node["string_ids"].add(raw["name_string_id"])
            node["node_types"].add(raw["node_type"])
            node["ages"].add(raw["age_id"])
            node["producers"].add(raw["building_id"])
            node["links"].add((raw["link_id"], raw["link_node_type"]))
            if raw["node_status"] != "NotAvailable":
                node["civs_available"].add(civ)
                available[category].add(int(numeric))
        civ_available[civ] = available
    for (category, numeric), node in nodes.items():
        entry = data["data"].get(category, {}).get(str(numeric))
        if entry:
            node["internal_name"] = entry["internal_name"]
    return data, strings, nodes, civ_available


def build_working_tables(con):
    """Create temp tables: ev (one row per event) and eo (E2O with the object's type)."""
    con.execute("PRAGMA cache_size=-512000")
    con.execute("CREATE TEMP TABLE ob(id TEXT PRIMARY KEY, type TEXT) WITHOUT ROWID")
    con.execute("INSERT INTO ob SELECT ocel_id, ocel_type FROM object")
    con.execute("""CREATE TEMP TABLE ev0(id TEXT PRIMARY KEY, type TEXT, action TEXT, phase TEXT,
                   name TEXT, ts INT, seq INT) WITHOUT ROWID""")
    for ocel_type, type_map in con.execute(
            "SELECT ocel_type, ocel_type_map FROM event_map_type").fetchall():
        parsed = parse_event_type(ocel_type) or (None, None, None)
        con.execute(
            f"""INSERT INTO ev0 SELECT ocel_id, ?, ?, ?, ?, unixepoch(ocel_time),
                CAST(substr(ocel_id, length(rtrim(ocel_id, '0123456789')) + 1) AS INT)
                FROM "event_{type_map}" """,
            (ocel_type, *parsed))
    con.execute("""CREATE TEMP TABLE eo AS
                   SELECT l.ocel_event_id AS id, l.ocel_object_id AS oid, ob.type AS otype
                   FROM event_object l LEFT JOIN ob ON ob.id = l.ocel_object_id""")
    con.execute("""CREATE TEMP TABLE es AS
                   SELECT id, MIN(oid) AS session, COUNT(*) AS n FROM eo
                   WHERE otype = 'Session' GROUP BY id""")
    con.execute("CREATE INDEX temp.es_id ON es(id)")
    con.execute("""CREATE TEMP TABLE ev AS
                   SELECT ev0.*, es.session, es.n AS n_sessions,
                          substr(es.session, 2, instr(es.session, '_') - 2) AS match_id
                   FROM ev0 LEFT JOIN es ON es.id = ev0.id""")
    con.execute("CREATE INDEX temp.ev_id ON ev(id)")
    con.execute("CREATE INDEX temp.ev_action ON ev(action, phase)")
    con.execute("CREATE INDEX temp.eo_id ON eo(id)")
    con.execute("ANALYZE temp")


def section_tables(con, r):
    r.h(2, "1. Tables, columns, row counts")
    tables = [row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    indexes = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
    counts, columns = {}, {}
    for table in tables:
        counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        columns[table] = ", ".join(
            f"{row[1]} {row[2]}" for row in con.execute(f'PRAGMA table_info("{table}")'))
    core = ["event", "object", "event_object", "object_object", "event_map_type", "object_map_type"]
    event_tables = [t for t in tables if t.startswith("event_") and t not in core]
    object_tables = [t for t in tables if t.startswith("object_") and t not in core]
    other = [t for t in tables if t not in core + event_tables + object_tables]
    r.p(f"{len(tables)} tables: {len(core)} core, {len(event_tables)} `event_<Type>`, "
        f"{len(object_tables)} `object_<Type>`, {len(other)} other. {indexes} indexes, so "
        "joins on the source are slow unless they go through temp tables.")
    r.p(md_table(["Core table", "Columns", "Rows"],
                 [(t, columns[t], f"{counts[t]:,}") for t in core]))
    event_columns = collections.Counter(columns[t] for t in event_tables)
    object_columns = collections.Counter(columns[t] for t in object_tables)
    r.p(md_table(["Family", "Column signature", "Tables", "Total rows"],
                 [("event_<Type>", sig, n,
                   f"{sum(counts[t] for t in event_tables if columns[t] == sig):,}")
                  for sig, n in event_columns.items()] +
                 [("object_<Type>", sig, n,
                   f"{sum(counts[t] for t in object_tables if columns[t] == sig):,}")
                  for sig, n in object_columns.items()]))
    r.p(fold(f"All {len(event_tables) + len(object_tables)} per-type tables with columns and rows",
             md_table(["Table", "Columns", "Rows"],
                      [(t, columns[t], f"{counts[t]:,}") for t in event_tables + object_tables])))


def section_events(con, r):
    r.h(2, "2. Event types")
    types = con.execute("""SELECT type, action, phase, name, COUNT(*) FROM ev
                           GROUP BY type ORDER BY type""").fetchall()
    total = sum(row[4] for row in types)
    r.p(f"{len(types)} event types with {total:,} events.")
    rows = collections.defaultdict(lambda: [0, 0])
    for _, action, phase, _, n in types:
        rows[(action or "UNPARSED", phase or "-")][0] += 1
        rows[(action or "UNPARSED", phase or "-")][1] += n
    r.h(3, "2.1 Inferred patterns")
    r.p(md_table(["Action", "Phase", "Types", "Events", "Share"],
                 [(a, ph, c, f"{n:,}", pct(n, total))
                  for (a, ph), (c, n) in sorted(rows.items(), key=lambda kv: -kv[1][1])]))

    r.h(3, "2.2 Comparison with the patterns in the spec")
    present = {t: sum(1 for row in types if row[0].startswith(t + " ") or row[0] == t)
               for t in SPEC_PREFIXES}
    r.p("Spec patterns with no event type in the data: " +
        (", ".join(f"`{t} …`" for t, n in present.items() if n == 0) or "none") + ".")
    no_spec = [row for row in types if not SPEC_PATTERN.match(row[0])]
    no_grammar = [row for row in types if row[1] is None]
    by_prefix = collections.Counter()
    for row in no_spec:
        by_prefix[(row[1], row[2])] += row[4]
    r.p(f"{len(no_spec)} types fit no pattern from the spec. All of them fit the grammar "
        "inferred here, which also covers `Start Queue …`, `Set Gather Point …` and "
        "`Create Initial …`:")
    r.p(md_table(["Action", "Phase", "Types", "Events"],
                 [(a, ph or "-", sum(1 for row in no_spec if (row[1], row[2]) == (a, ph)), f"{n:,}")
                  for (a, ph), n in by_prefix.most_common()]))
    r.p(fold(f"All {len(no_spec)} types outside the spec patterns",
             md_table(["Event type", "Events"], [(row[0], f"{row[4]:,}") for row in no_spec])))
    r.p(f"Types that fit neither the spec nor the inferred grammar: {len(no_grammar)}" +
        (": " + ", ".join(f"`{row[0]}`" for row in no_grammar) if no_grammar else "") + ".")
    placeholder = [row for row in types if row[3] in ("", "None")]
    r.p("Types without a usable object name (`None` or an empty suffix):")
    r.p(md_table(["Event type", "Events"],
                 [(f"`{row[0]}`", f"{row[4]:,}") for row in placeholder]))

    r.h(3, "2.3 Types per action and object")
    for action in ("QUEUE", "RESEARCH", "BUILD"):
        names = collections.defaultdict(dict)
        for _, a, phase, name, n in types:
            if a == action:
                names[name][phase] = n
        ordered = sorted(names.items(), key=lambda kv: (-max(kv[1].values()), kv[0]))
        equal = sum(1 for _, ph in ordered if len(set(ph.values())) == 1)
        r.p(fold(f"{action}: {len(ordered)} objects; phase counts identical for {equal}",
                 md_table(["Object", "COMMAND", "START", "COMPLETE"],
                          [(show(n), f"{p.get('COMMAND', 0):,}", f"{p.get('START', 0):,}",
                            f"{p.get('COMPLETE', 0):,}") for n, p in ordered])))
    for action in ("GATHER", "SET_GATHER_POINT", "CREATE_INITIAL"):
        ordered = sorted(((row[3], row[4]) for row in types if row[1] == action),
                         key=lambda kv: -kv[1])
        r.p(fold(f"{action}: {len(ordered)} types",
                 md_table(["Suffix", "Events"], [(show(n), f"{c:,}") for n, c in ordered])))


def pair_deltas(con, action):
    """Yield per name a Counter of Complete minus Start, pairing the i-th of each per session."""
    grouped = collections.defaultdict(lambda: ([], []))
    for session, name, phase, ts in con.execute(
            "SELECT session, name, phase, ts FROM ev WHERE action=? AND phase IN ('START','COMPLETE')",
            (action,)):
        grouped[(session, name)][0 if phase == "START" else 1].append(ts)
    deltas = collections.defaultdict(collections.Counter)
    for (_, name), (starts, completes) in grouped.items():
        for start, complete in zip(sorted(starts), sorted(completes)):
            deltas[name][complete - start] += 1
    return deltas


def expected_times(data, name_index, action, name):
    """Times from data.json for all nodes carrying this name. Empty if the name is unmapped."""
    times = set()
    for node in name_index[action].get(normalize(name), []):
        key = str(node["id"])
        if node["category"] == "Unit" and action == "RESEARCH":
            upgrade = data["data"]["unit_upgrades"].get(key)
            if upgrade:
                times.add(upgrade["ResearchTime"])
        elif node["category"] == "Tech":
            times.add(data["data"]["Tech"][key]["ResearchTime"])
        elif node["category"] in data["data"] and key in data["data"][node["category"]]:
            times.add(data["data"][node["category"]][key]["TrainTime"])
    return times


def section_time(con, r, data, name_index):
    r.h(2, "3. Timestamps and how events were generated")
    start, end = con.execute(
        "SELECT datetime(MIN(ts),'unixepoch'), datetime(MAX(ts),'unixepoch') FROM ev").fetchone()
    resolution = con.execute("SELECT COUNT(*) FROM event_CommandQueueVillager "
                             "WHERE length(ocel_time) <> 19").fetchone()[0]
    r.p(f"Times are in the per-type tables only, as `YYYY-MM-DD HH:MM:SS` text (1 s resolution; "
        f"{resolution} villager rows deviate). Range: {start} to {end}. "
        "`event` itself has no time column.")
    durations = [row[1] - row[0] for row in con.execute(
        "SELECT MIN(ts), MAX(ts) FROM ev GROUP BY match_id")]
    quantiles = statistics.quantiles(durations, n=4) if len(durations) > 1 else [0, 0, 0]
    short = sum(1 for d in durations if d < 300)
    r.p(f"Span per match (last minus first event, s): min {min(durations)}, Q1 {quantiles[0]:.0f}, "
        f"median {quantiles[1]:.0f}, Q3 {quantiles[2]:.0f}, max {max(durations)}. "
        f"{short} of {len(durations)} matches span under 300 s.")
    violations = con.execute("""SELECT COUNT(*) FROM (SELECT ts, LAG(ts) OVER
        (PARTITION BY match_id ORDER BY seq) AS previous FROM ev) WHERE ts < previous""").fetchone()[0]
    r.p(f"Event IDs (`e_<match>_<n>`) follow time: {violations} cases where a higher `n` has an "
        "earlier time within a match. So `n` is a usable tie-breaker within a second.")

    r.h(3, "3.1 Start to Complete durations against data.json")
    r.p("Starts and completes are paired per session and type in time order (the i-th start with "
        "the i-th complete). A *fixed* share near 100% means the duration is a constant, not a "
        "measurement, because real durations would vary with civ bonuses and with idle or "
        "cancelled production.")
    for action in ("QUEUE", "RESEARCH"):
        deltas = pair_deltas(con, action)
        rows, fixed, agree, comparable, weight, fixed_weight = [], 0, 0, 0, 0, 0
        zero_types = []
        for name, counter in sorted(deltas.items(), key=lambda kv: -sum(kv[1].values())):
            n = sum(counter.values())
            mode, mode_count = counter.most_common(1)[0]
            weight += n
            fixed_weight += mode_count
            fixed += mode_count == n
            if mode == 0:
                zero_types.append((name, n))
            times = expected_times(data, name_index, action, name)
            if times:
                comparable += 1
                agree += mode in times
            rows.append((show(name), f"{n:,}", mode, pct(mode_count, n),
                         ", ".join(str(t) for t in sorted(times)) or "(unmapped)",
                         "" if not times else ("yes" if mode in times else "NO")))
        r.p(f"**{action}:** {fixed} of {len(deltas)} types have one single duration, and the most "
            f"common duration covers {pct(fixed_weight, weight)} of all events. The log's duration "
            f"equals a data.json time for {agree} of {comparable} mapped types.")
        zero_events = sum(n for _, n in zero_types)
        r.p(f"{len(zero_types)} {action} types have a duration of **exactly 0 s**, covering "
            f"{zero_events:,} pairs ({pct(zero_events, weight)}): " +
            ", ".join(show(name) for name, _ in zero_types) + ".")
        r.p(fold(f"{action} durations per type",
                 md_table(["Object", "Pairs", "Most common (s)", "Share", "data.json time (s)",
                           "Equal?"], rows)))

    builds = con.execute("""
        SELECT ev.name, MAX(CASE WHEN ev.phase='COMPLETE' THEN ev.ts END)
                      - MIN(CASE WHEN ev.phase='START' THEN ev.ts END)
        FROM ev JOIN eo ON eo.id = ev.id AND eo.otype = ev.name
        WHERE ev.action='BUILD' GROUP BY eo.oid""").fetchall()
    per_name = collections.defaultdict(collections.Counter)
    for name, delta in builds:
        per_name[name][delta] += 1
    rows = []
    for name, counter in sorted(per_name.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(counter.values())
        mode, mode_count = counter.most_common(1)[0]
        times = expected_times(data, name_index, "BUILD", name)
        rows.append((show(name), f"{n:,}", mode, pct(mode_count, n), min(counter), max(counter),
                     ", ".join(str(t) for t in sorted(times)) or "(unmapped)"))
    zero_builds = [row[0] for row in rows if row[5] == 0]
    max_equal = sum(1 for row in rows if str(row[5]) in row[6].split(", "))
    r.p("**BUILD:** paired through the building object. Durations vary, presumably with the number "
        f"of builders. The longest duration equals a data.json time for {max_equal} of {len(rows)} "
        f"types. Types that always take 0 s: {', '.join(zero_builds) or 'none'}.")
    r.p("**Reading:** one constant per type, with no effect from civ bonuses, and exactly 0 s for "
        "many types, is what a lookup table with missing entries would produce. This is an "
        "inference from the numbers. The dataset's generation code was not inspected. It means "
        "COMPLETE times, and QUEUE START times that depend on them, are derived values and not "
        "observations.")
    r.p(fold("BUILD durations per type",
             md_table(["Object", "Buildings", "Most common (s)", "Share", "Min", "Max",
                       "data.json time (s)"], rows)))

    r.h(3, "3.2 Command to Start lag and producer queues")
    lag = collections.Counter()
    grouped = collections.defaultdict(lambda: ([], []))
    for session, name, phase, ts in con.execute(
            "SELECT session, name, phase, ts FROM ev WHERE action='QUEUE' AND phase IN ('COMMAND','START')"):
        grouped[(session, name)][0 if phase == "COMMAND" else 1].append(ts)
    for commands, starts in grouped.values():
        for command, start in zip(sorted(commands), sorted(starts)):
            lag[start - command] += 1
    total_lag = sum(lag.values())
    negative = sum(n for d, n in lag.items() if d < 0)
    zero = lag[0]
    r.p(f"QUEUE: START equals COMMAND for {pct(zero, total_lag)} of pairs, is later for "
        f"{pct(total_lag - zero - negative, total_lag)} and earlier for {pct(negative, total_lag)}. "
        "This pairing ignores which producer was used, so individual pairs are approximate.")
    producer_rows = con.execute("""
        SELECT ev.session, eo.oid, ev.phase, ev.ts FROM ev JOIN eo ON eo.id = ev.id
        WHERE ev.action='QUEUE' AND ev.phase IN ('START','COMPLETE')
          AND (eo.otype IS NULL OR eo.otype NOT IN ('Match','Session','Villager'))
        ORDER BY ev.session, eo.oid, ev.ts, ev.seq""")
    overlap, touching, gap, previous_key, starts_seen, last_complete = 0, 0, 0, None, 0, None
    for session, oid, phase, ts in producer_rows:
        key = (session, oid)
        if key != previous_key:
            previous_key, last_complete = key, None
        if phase == "COMPLETE":
            last_complete = ts
        elif last_complete is not None:
            starts_seen += 1
            if ts == last_complete:
                touching += 1
            elif ts > last_complete:
                gap += 1
            else:
                overlap += 1
    r.p(f"Per producer object, compared with the previous completion ({starts_seen:,} starts): "
        f"{pct(touching, starts_seen)} start in exactly the same second, {pct(gap, starts_seen)} "
        f"after a gap, {pct(overlap, starts_seen)} before it finished. This fits a simulated "
        "first-in-first-out queue.")

    r.h(3, "3.3 Events after the last player input")
    inputs = dict(con.execute("""SELECT session, MAX(ts) FROM ev
        WHERE phase='COMMAND' OR (action='BUILD' AND phase='START') OR action='SET_GATHER_POINT'
        GROUP BY session"""))
    after, completes = 0, 0
    for session, ts in con.execute("SELECT session, ts FROM ev WHERE phase='COMPLETE'"):
        completes += 1
        after += session in inputs and ts > inputs[session]
    r.p(f"{after:,} of {completes:,} COMPLETE events ({pct(after, completes)}) fall after the "
        "session's last player input. COMMAND, START and COMPLETE counts per type also match "
        "exactly (section 2.3), so nothing shows cancelled production.")


def section_objects(con, r):
    r.h(2, "4. Objects and links")
    r.h(3, "4.1 Object types")
    linked = {row[0]: row[1:] for row in con.execute(
        "SELECT otype, COUNT(*), COUNT(DISTINCT oid) FROM eo WHERE otype IS NOT NULL GROUP BY otype")}
    created = collections.Counter()
    for action, phase, name, n in con.execute("""SELECT action, phase, name, COUNT(*) FROM ev
            WHERE (action IN ('QUEUE','BUILD') AND phase='START') OR action='CREATE_INITIAL'
            GROUP BY action, phase, name"""):
        created[name] += n
    rows = []
    for object_type, n in con.execute(
            "SELECT type, COUNT(*) FROM ob GROUP BY type ORDER BY 2 DESC, 1").fetchall():
        e2o_rows, referenced = linked.get(object_type, (0, 0))
        rows.append((object_type, f"{n:,}", f"{created[object_type]:,}", f"{referenced:,}",
                     f"{e2o_rows:,}"))
    declared_total = sum(n for _, n in con.execute("SELECT type, COUNT(*) FROM ob GROUP BY type"))
    r.p(f"{len(rows)} object types with {declared_total:,} objects (the spec expected about 30). "
        "*Created by events* counts START of QUEUE and BUILD plus CREATE_INITIAL events for that "
        "name. *Referenced* counts objects that occur in `event_object` at all.")
    r.p(md_table(["Object type", "Objects", "Created by events", "Referenced", "E2O rows"], rows))
    never = [row[0] for row in rows if row[3] == "0"]
    r.p(f"{len(never)} object types are never referenced by an event: " +
        (", ".join(never) or "none") + ".")

    r.h(3, "4.2 `event_object` sample")
    r.p(md_table(["ocel_event_id", "ocel_object_id", "ocel_qualifier"],
                 [(a, b, repr(c)) for a, b, c in con.execute(
                     "SELECT * FROM event_object WHERE rowid <= 12 ORDER BY rowid")]))
    r.h(3, "4.3 `object_object` sample")
    r.p(md_table(["ocel_source_id", "ocel_target_id", "ocel_qualifier"],
                 [(a, b, repr(c)) for a, b, c in con.execute(
                     "SELECT * FROM object_object WHERE rowid <= 12 ORDER BY rowid")]))
    r.p(md_table(["Table", "Distinct qualifiers", "Values"], [
        (t, len(v), ", ".join(repr(x[0]) for x in v)) for t, v in (
            ("event_object", con.execute(
                "SELECT DISTINCT ocel_qualifier FROM event_object").fetchall()),
            ("object_object", con.execute(
                "SELECT DISTINCT ocel_qualifier FROM object_object").fetchall()))]))

    r.h(3, "4.4 Objects missing from `object` (`UO` IDs)")
    undeclared = con.execute("SELECT COUNT(*), COUNT(DISTINCT oid) FROM eo WHERE otype IS NULL").fetchone()
    pattern_ok = con.execute(
        "SELECT COUNT(*) FROM eo WHERE otype IS NULL AND oid NOT GLOB 'M*_UO*'").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM eo").fetchone()[0]
    events_with = con.execute("SELECT COUNT(DISTINCT id) FROM eo WHERE otype IS NULL").fetchone()[0]
    n_events = con.execute("SELECT COUNT(*) FROM ev").fetchone()[0]
    r.p(f"{undeclared[0]:,} of {total:,} E2O rows point to {undeclared[1]:,} object IDs that "
        f"`object` does not contain, which touches {events_with:,} events "
        f"({pct(events_with, n_events)}). {pattern_ok} of those IDs do not have the form "
        "`M<match>_UO<n>`. These IDs carry no type.")
    r.p(md_table(["Event type", "E2O rows to UO"], [
        (show(a), f"{b:,}") for a, b in con.execute("""SELECT ev.type, COUNT(*) FROM eo
            JOIN ev ON ev.id = eo.id WHERE eo.otype IS NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 15""")]))

    r.h(3, "4.5 What each event links to")
    r.p("Share of events per pattern with a link of the given kind. Exactly one session per event: " +
        ("yes." if con.execute("SELECT COUNT(*) FROM ev WHERE n_sessions IS NOT 1").fetchone()[0] == 0
         else "**no**."))
    profile = collections.defaultdict(collections.Counter)
    totals = collections.Counter()
    for action, phase, n in con.execute("SELECT action, phase, COUNT(*) FROM ev GROUP BY 1, 2"):
        totals[(action, phase)] = n
    for action, phase, otype, n in con.execute("""
            SELECT ev.action, ev.phase,
                   CASE WHEN eo.otype IS NULL THEN 'UO'
                        WHEN eo.otype IN ('Match','Session','Player','Villager','Town Center') THEN eo.otype
                        ELSE 'Other' END,
                   COUNT(DISTINCT eo.id)
            FROM eo JOIN ev ON ev.id = eo.id GROUP BY 1, 2, 3"""):
        profile[(action, phase)][otype] = n
    kinds = ["Player", "Session", "Match", "Villager", "Town Center", "Other", "UO"]
    r.p(md_table(["Action", "Phase", "Events"] + kinds, [
        (a, p or "-", f"{totals[(a, p)]:,}", *[pct(profile[(a, p)][k], totals[(a, p)]) for k in kinds])
        for (a, p) in sorted(totals, key=lambda k: (k[0], k[1] or ""))]))
    mismatch = con.execute("""SELECT COUNT(*) FROM eo JOIN ev ON ev.id = eo.id
        WHERE eo.otype='Player' AND ev.session NOT LIKE '%\\_' || substr(eo.oid, 2) ESCAPE '\\'""").fetchone()[0]
    r.p(f"Player links that disagree with the player encoded in the session ID: {mismatch:,}.")

    r.h(3, "4.6 `object_object` by type pair")
    r.p(md_table(["Source", "Target", "Rows"], [
        (a or "UO", b or "UO", f"{n:,}") for a, b, n in con.execute("""
            SELECT CASE WHEN s.type IS NULL THEN NULL
                        WHEN s.type IN ('Player','Session','Match','Villager') THEN s.type ELSE 'Other' END,
                   CASE WHEN t.type IS NULL THEN NULL
                        WHEN t.type IN ('Player','Session','Match','Villager') THEN t.type ELSE 'Other' END,
                   COUNT(*)
            FROM object_object l LEFT JOIN ob s ON s.id = l.ocel_source_id
            LEFT JOIN ob t ON t.id = l.ocel_target_id GROUP BY 1, 2 ORDER BY 3 DESC""")]))

    r.h(3, "4.7 Sessions and players")
    per_match = con.execute("""SELECT n, COUNT(*) FROM (SELECT COUNT(*) n FROM ob WHERE type='Session'
        GROUP BY substr(id, 2, instr(id, '_') - 2)) GROUP BY 1""").fetchall()
    silent = con.execute("""SELECT COUNT(*) FROM ob WHERE type='Session' AND id NOT IN
        (SELECT session FROM ev WHERE phase='COMMAND')""").fetchone()[0]
    counts = [row[0] for row in con.execute(
        "SELECT COUNT(*) FROM ev WHERE phase='COMMAND' GROUP BY session")]
    r.p(f"Sessions per match: {', '.join(f'{a} sessions in {b} matches' for a, b in per_match)}. "
        f"Sessions with no COMMAND event: {silent}. COMMAND events per session: min {min(counts)}, "
        f"median {statistics.median(counts):.0f}, max {max(counts)} (among sessions with any).")
    r.p("Largest sessions by COMMAND events: " + ", ".join(
        f"`{a}` ({b:,})" for a, b in con.execute("""SELECT session, COUNT(*) FROM ev
            WHERE phase='COMMAND' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5""")) + ".")

    r.h(3, "4.8 Starting state from CREATE_INITIAL")
    r.p("All CREATE_INITIAL events share the first timestamp of their match: " + (
        "yes." if con.execute("""SELECT COUNT(*) FROM ev i JOIN (SELECT match_id, MIN(ts) t FROM ev GROUP BY 1) m
            ON m.match_id = i.match_id WHERE i.action='CREATE_INITIAL' AND i.ts <> m.t""").fetchone()[0] == 0
        else "**no**."))
    vil = collections.Counter(row[0] for row in con.execute(
        """SELECT COUNT(*) FROM ev WHERE type='Create Initial Villager' GROUP BY session"""))
    sessions = con.execute("SELECT COUNT(*) FROM ob WHERE type='Session'").fetchone()[0]
    vil[0] = sessions - sum(vil.values())
    r.p(md_table(["Initial villagers", "Sessions"], sorted(vil.items())))
    initial = dict(con.execute(
        "SELECT name, COUNT(DISTINCT session) FROM ev WHERE action='CREATE_INITIAL' GROUP BY name"))
    only_initial = sorted(name for (name,) in con.execute(
        "SELECT DISTINCT name FROM ev WHERE action='CREATE_INITIAL'").fetchall()
        if not con.execute("SELECT 1 FROM ev WHERE action IN ('QUEUE','BUILD') AND name=? LIMIT 1",
                           (name,)).fetchone())
    r.p("Sessions per initial object:")
    r.p(md_table(["Object", "Sessions", "Only ever appears as initial?"],
                 [(n, c, "yes" if n in only_initial else "")
                  for n, c in sorted(initial.items(), key=lambda kv: -kv[1])]))


def section_attributes(con, r):
    r.h(2, "5. Which attributes are filled")
    event_columns = collections.Counter()
    for (type_map,) in con.execute("SELECT ocel_type_map FROM event_map_type").fetchall():
        for row in con.execute(f'PRAGMA table_info("event_{type_map}")'):
            event_columns[row[1]] += 1
    empty_times = sum(con.execute(
        f"""SELECT COUNT(*) FROM "event_{m}" WHERE ocel_time IS NULL OR ocel_time=''""").fetchone()[0]
        for (m,) in con.execute("SELECT ocel_type_map FROM event_map_type").fetchall())
    object_tables = [m for (m,) in con.execute("SELECT ocel_type_map FROM object_map_type").fetchall()]
    change_filled = sum(con.execute(
        f'SELECT COUNT(*) FROM "object_{m}" WHERE ocel_change_field IS NOT NULL').fetchone()[0]
        for m in object_tables)
    object_times = collections.Counter()
    for m in object_tables:
        for ts, n in con.execute(f'SELECT ocel_time, COUNT(*) FROM "object_{m}" GROUP BY 1'):
            object_times[ts] += n
    extra = sorted(c for c in event_columns if c not in ("ocel_id", "ocel_time"))
    r.p(md_table(["Attribute", "Where", "Filled"], [
        ("ocel_time", "all event_<Type>", f"100% ({empty_times} empty)"),
        ("ocel_time", "all object_<Type>", f"{len(object_times)} distinct value(s): "
         + ", ".join(f"`{k}` × {v:,}" for k, v in object_times.most_common(3))),
        ("ocel_change_field", "all object_<Type>", f"{change_filled} non-null"),
        ("ocel_qualifier", "event_object, object_object", "only empty strings (section 4)"),
    ]))
    r.p("Event tables have no attribute columns beyond `ocel_id` and `ocel_time`" +
        (f"; extra columns found: {extra}." if extra else ".") +
        " Object tables have no others beyond the two above. So civ, map, Elo and winner are not "
        "just empty: the columns do not exist. Amounts (gathered resources, unit deaths, damage) "
        "are not recorded either.")


def section_data_json(r, data, strings, nodes, civ_available):
    r.h(2, "6. Structure of data.json and the tree files")
    r.p(md_table(["Key", "Type", "Entries", "Content"], [
        ("age_names", "dict", len(data["age_names"]), "string IDs of the age names per era"),
        ("civs", "dict", len(data["civs"]), "per civ: Building, Tech, Unit (ID lists), era, name and help string IDs"),
        ("data", "dict", len(data["data"]), ", ".join(f"{k}: {len(v)}" for k, v in data["data"].items())),
        ("tech_tree_strings", "dict", len(data["tech_tree_strings"]), "string IDs of UI labels"),
    ]))
    examples = [("Building", "12"), ("Unit", "4"), ("Tech", "22"), ("unit_upgrades", "24")]
    for category, key in examples:
        entry = data["data"][category][key]
        short = {k: v for k, v in entry.items() if k not in ("Armours", "Attacks")}
        r.p(f"Example `data.{category}[\"{key}\"]` (`Armours`, `Attacks` omitted):\n\n"
            f"```json\n{json.dumps(short, sort_keys=True)}\n```")
    civ = "Britons"
    r.p(f"Example `civs[\"{civ}\"]` (lists shortened):\n\n```json\n" + json.dumps({
        k: (v[:6] + ["…"] if isinstance(v, list) else v) for k, v in data["civs"][civ].items()},
        sort_keys=True) + "\n```")

    r.h(3, "6.1 Field availability")
    categories = ["Building", "Unit", "Tech", "unit_upgrades"]
    def has(category, field):
        entries = data["data"][category].values()
        return f"{sum(field in e for e in entries)}/{len(entries)}"
    r.p(md_table(["Field"] + categories, [
        (f, *[has(c, f) for c in categories])
        for f in ["Cost", "TrainTime", "ResearchTime", "LanguageNameId", "internal_name"]]))
    keys = set()
    for category in categories:
        for entry in data["data"][category].values():
            keys |= set(entry)
    wanted = ["age", "Age", "required_age", "prerequisite", "Prerequisite", "requires", "Requires",
              "produced_at", "building", "Building", "link", "name"]
    r.p("Research times exist (`Tech.ResearchTime`, `unit_upgrades.ResearchTime`). "
        f"Fields in data.json for age, prerequisite, producer or display name: "
        f"{sorted(k for k in keys if k in wanted) or 'none'}. All four must come from elsewhere.")

    r.h(3, "6.2 IDs")
    b, t, u = (set(data["data"][k]) for k in ("Building", "Tech", "Unit"))
    r.p(f"IDs are only unique per category. Overlaps: Building and Tech {len(b & t)}, "
        f"Unit and Tech {len(u & t)}, Building and Unit {len(b & u)}. `game_object.id` therefore "
        "needs its own key.")
    upgrades = data["data"]["unit_upgrades"]
    in_tech = sum(str(e["ID"]) in t for e in upgrades.values())
    r.p(f"`unit_upgrades` is keyed by the ID of the resulting **unit**, while its inner `ID` is a "
        f"tech ID. Only {in_tech} of {len(upgrades)} of these are in `data.Tech`, and civ lists "
        "contain upgrades as Unit IDs. A unit upgrade appears in the log as `Research`, so it maps "
        "to a Unit ID.")

    r.h(3, "6.3 String table (found)")
    def direct(category):
        entries = list(data["data"][category].values())
        return f"{sum(str(e['LanguageNameId']) in strings for e in entries)}/{len(entries)}"
    ok = sum(all(str(s) in strings for s in n["string_ids"]) for n in nodes.values())
    same = sum(all(strings.get(str(s), "").replace("<br>\n", " ").replace("<br>", " ").strip() in n["names"]
                   for s in n["string_ids"]) for n in nodes.values())
    multi = sorted(n["name"] for n in nodes.values() if len(n["names"]) > 1)
    r.p(f"`aoe2techtree.net/data/locales/en/strings.json` holds {len(strings):,} strings. "
        f"`LanguageNameId` does **not** reliably resolve in it: Building {direct('Building')}, "
        f"Unit {direct('Unit')}, Tech {direct('Tech')}. The site instead uses `name_string_id` "
        f"from the tree files, which resolves for {ok} of {len(nodes)} nodes and gives the node's "
        f"`name` for {same}. Nodes with different names across civs: {multi or 'none'}.")

    r.h(3, "6.4 Per-civ tree files: source for age, producer and prerequisites")
    r.p("`aoe2techtree.net/data/trees/<CIV>.json` has `buildings` and `units_techs`, with nodes "
        "like this:")
    tree = json.loads((TECHTREE_DIR / "trees/BRITONS.json").read_text())
    node = next(n for n in tree["units_techs"] if n["id"].startswith("Unit_24_"))
    r.p(f"```json\n{json.dumps(node, sort_keys=True)}\n```")
    r.p(md_table(["Field", "Use"], [
        ("name, name_string_id", "display name"),
        ("age_id (1-4)", "required_age"),
        ("building_id", "produced_at"),
        ("link_id + link_node_type", "the prerequisite drawn in the tree"),
        ("node_type", "Unit, UniqueUnit, RegionalUnit, UnitUpgrade, Research, UniqueTech, RegionalTech, BuildingTech, BuildingNonTech"),
        ("node_status", "availability for this civ (`NotAvailable` otherwise)"),
    ]))
    types = collections.Counter(t for n in nodes.values() for t in n["node_types"])
    r.p(f"{len(nodes)} distinct nodes across all civs: " +
        ", ".join(f"{k}: {v}" for k, v in types.most_common()) + ".")
    varying = lambda field: sorted(n["name"] for n in nodes.values() if len(n[field]) > 1)
    ages = varying("ages")
    producers = varying("producers")
    links = varying("links")
    r.p(f"Values that differ by civ, so one value per `game_object` is a simplification: "
        f"age for {len(ages)} nodes ({', '.join(ages[:12])}{', …' if len(ages) > 12 else ''}); "
        f"producer for {len(producers)} ({', '.join(producers[:12])}{', …' if len(producers) > 12 else ''}); "
        f"prerequisite link for {len(links)} ({', '.join(links[:12])}{', …' if len(links) > 12 else ''}).")
    mismatch = 0
    missing_nodes = 0
    for civ, lists in data["civs"].items():
        for category in ("Building", "Tech", "Unit"):
            listed = set(lists[category])
            mismatch += len(listed ^ civ_available[civ][category])
            missing_nodes += len(listed - {n["id"] for n in nodes.values() if n["category"] == category})
    r.p(f"`civs[...]` lists against tree `node_status`: {mismatch} differences across all civs, "
        f"{missing_nodes} listed IDs without a tree node.")
    missing = [(k, n["name"]) for k, n in nodes.items() if n["internal_name"] is None and not
               (k[0] == "Unit" and str(k[1]) in data["data"]["unit_upgrades"])]
    r.p(f"Tree nodes with no entry in data.json: {len(missing)}" +
        (" (" + ", ".join(f"{n}" for _, n in missing[:20]) + ")" if missing else "") + ".")
    r.p("**Limits of `link_id`:** it holds what the tree draws, such as Archery Range after "
        "Barracks. Rules with several requirements are not expressed, for example that Feudal Age "
        "needs a set number of earlier buildings. These need a small hand-maintained CSV.")


def build_name_index(nodes, data):
    """action -> normalized name -> nodes that the action can refer to.

    Unit upgrades are researched, so every Unit node with a `unit_upgrades` entry also
    counts for RESEARCH. This includes elite unique units, whose node_type is UniqueUnit.
    """
    upgrades = data["data"]["unit_upgrades"]
    index = {action: collections.defaultdict(list) for action in ACTION_NODE_TYPES}
    for node in nodes.values():
        is_upgrade = node["category"] == "Unit" and str(node["id"]) in upgrades
        for action, node_types in ACTION_NODE_TYPES.items():
            if node["node_types"] & node_types or (action == "RESEARCH" and is_upgrade):
                for name in node["names"]:
                    index[action][normalize(name)].append(node)
    return index


def section_names(con, r, data, nodes, name_index):
    r.h(2, "7. Name matching")
    names_by_source = {}
    weights = {}
    for action in ("QUEUE", "RESEARCH", "BUILD"):
        rows = con.execute("""SELECT name, COUNT(*) FROM ev WHERE action=? AND
            phase=(CASE ? WHEN 'BUILD' THEN 'START' ELSE 'COMMAND' END) GROUP BY name""",
                           (action, action)).fetchall()
        names_by_source[action] = sorted(n for n, _ in rows)
        weights[action] = dict(rows)
    names_by_source["CREATE_INITIAL"] = sorted(n for (n,) in con.execute(
        "SELECT DISTINCT name FROM ev WHERE action='CREATE_INITIAL'"))
    weights["CREATE_INITIAL"] = dict(con.execute(
        "SELECT name, COUNT(*) FROM ev WHERE action='CREATE_INITIAL' GROUP BY name"))
    names_by_source["OBJECT_TYPE"] = sorted(n for (n,) in con.execute(
        "SELECT DISTINCT type FROM ob WHERE type NOT IN ('Match','Player','Session')"))
    weights["OBJECT_TYPE"] = dict(con.execute("SELECT type, COUNT(*) FROM ob GROUP BY type"))

    internal = set()
    for category in ("Building", "Unit", "Tech", "unit_upgrades"):
        internal |= {e["internal_name"] for e in data["data"][category].values()}
    all_display = {name for n in nodes.values() for name in n["names"]}
    all_norm = {normalize(x) for x in all_display}

    def targets(source):
        """Names and normalized names available for a source. CREATE_INITIAL and objects may be anything."""
        if source in name_index:
            subset = {x["name"] for items in name_index[source].values() for x in items}
            subset |= {name for items in name_index[source].values() for x in items for name in x["names"]}
            return subset, {normalize(x) for x in subset}
        return all_display, all_norm

    r.p("Sources are the suffixes of QUEUE, RESEARCH, BUILD and CREATE_INITIAL types and the object "
        "types. Each action is matched only against nodes it can refer to (QUEUE against units, "
        "RESEARCH against techs and unit upgrades, BUILD against buildings). *Normalized* ignores "
        "case and all characters other than letters and digits. *Events matched* uses COMMAND "
        "events for QUEUE and RESEARCH, and START for BUILD, which has no COMMAND.")
    rows = []
    unmatched = {}
    for source, names in names_by_source.items():
        display, norm = targets(source)
        raw = sum(n in internal for n in names)
        exact = [n for n in names if n in display]
        normed = [n for n in names if normalize(n) in norm and n != ""]
        unmatched[source] = [n for n in names if n not in normed]
        w = weights[source]
        total = sum(w.get(n, 0) for n in names)
        rows.append((source, len(names), f"{raw} ({pct(raw, len(names))})",
                     f"{len(exact)} ({pct(len(exact), len(names))})",
                     f"{len(normed)} ({pct(len(normed), len(names))})",
                     pct(sum(w.get(n, 0) for n in exact), total),
                     pct(sum(w.get(n, 0) for n in normed), total)))
    r.p(md_table(["Source", "Names", "data.json `internal_name`", "Tree name, exact",
                  "Tree name, normalized", "Events matched, exact", "Events matched, normalized"], rows))

    combined = sorted(set().union(*names_by_source.values()))
    exact_all = sum(n in all_display for n in combined)
    norm_all = sum(normalize(n) in all_norm and n != "" for n in combined)
    r.p(f"All distinct names together, against any node: {len(combined)} names, "
        f"{sum(n in internal for n in combined)} in `internal_name`, {exact_all} exact, "
        f"{norm_all} normalized.")

    r.h(3, "7.1 Names not matched after normalization")
    r.p("*Closest name* is a string-similarity hint for building `manual_mapping.csv`, not a "
        "mapping decision.")
    rows = []
    for source, names in unmatched.items():
        display, _ = targets(source)
        for name in names:
            hint = difflib.get_close_matches(name, sorted(display), n=1, cutoff=0.6)
            rows.append((source, show(name), f"{weights[source].get(name, 0):,}",
                         hint[0] if hint else ""))
    r.p(md_table(["Source", "Name", "Count", "Closest name"], rows))

    r.h(3, "7.2 Exact misses that normalization resolves")
    rows = []
    for source in ("QUEUE", "RESEARCH", "BUILD"):
        display, _ = targets(source)
        for name in names_by_source[source]:
            if name and name not in display and normalize(name) in name_index[source]:
                rows.append((source, name, name_index[source][normalize(name)][0]["name"],
                             f"{weights[source].get(name, 0):,}"))
    r.p(md_table(["Source", "Log name", "Tree name", "Count"], rows))

    r.h(3, "7.3 Ambiguous names (one log name, several IDs)")
    rows = []
    for source in ("QUEUE", "RESEARCH", "BUILD"):
        for name in names_by_source[source]:
            candidates = name_index[source].get(normalize(name), [])
            if len(candidates) > 1:
                rows.append((source, name, ", ".join(
                    f"{c['category']} {c['id']} ({c['internal_name'] or '?'})"
                    for c in sorted(candidates, key=lambda c: (c["category"], c["id"])))))
    r.p(md_table(["Source", "Log name", "Candidate nodes"], rows) if rows else "None.")


def section_civ_preview(con, r, data, nodes, civ_available, name_index):
    r.h(2, "8. Preview: feasibility of civ narrowing")
    r.p("This is not the Phase 4 result. It is a rough check of whether the idea works with a "
        "2026 tech tree on 2023 replays. Names use normalized matching, and a name counts as "
        "available to a civ if any of its candidate IDs is available.")
    used = collections.defaultdict(set)
    for session, action, name in con.execute("""SELECT DISTINCT session, action, name FROM ev
            WHERE (action IN ('QUEUE','RESEARCH') AND phase='COMMAND') OR (action='BUILD' AND phase='START')"""):
        if name_index[action].get(normalize(name)):
            used[session].add((action, normalize(name)))
    civs_for = {}
    for items in used.values():
        for key in items:
            if key not in civs_for:
                action, norm = key
                civs_for[key] = {
                    civ for civ in data["civs"]
                    if any(node["id"] in civ_available[civ][node["category"]]
                           for node in name_index[action][norm])}
    all_civs = set(data["civs"])
    known_2023 = all_civs - ASSUMED_POST_REPLAY_CIVS
    sessions = con.execute("SELECT id FROM ob WHERE type='Session'").fetchall()
    camel_key = ("QUEUE", normalize("Camel Scout"))
    camel_rider_civs = {c for c in data["civs"] if CAMEL_RIDER_ID in civ_available[c]["Unit"]}
    civs_hypothesis = dict(civs_for)
    if camel_key in civs_hypothesis:
        civs_hypothesis[camel_key] = civs_for[camel_key] | camel_rider_civs
    sizes_all, sizes_2023, sizes_camel = (collections.Counter() for _ in range(3))
    zero_causes = collections.Counter()
    only_candidate, only_candidate_camel = collections.Counter(), collections.Counter()
    for (session,) in sessions:
        candidates, candidates_camel = set(all_civs), set(known_2023)
        for key in used[session]:
            candidates &= civs_for[key]
            candidates_camel &= civs_hypothesis[key]
        sizes_all[len(candidates)] += 1
        sizes_2023[len(candidates & known_2023)] += 1
        sizes_camel[len(candidates_camel)] += 1
        if len(candidates & known_2023) == 1:
            only_candidate[next(iter(candidates & known_2023))] += 1
        if len(candidates_camel) == 1:
            only_candidate_camel[next(iter(candidates_camel))] += 1
        if not candidates_camel:
            remaining = set(known_2023)
            for key in sorted(used[session], key=lambda k: (len(civs_hypothesis[k]), k)):
                if not remaining & civs_hypothesis[key]:
                    zero_causes[key[1]] += 1
                    break
                remaining &= civs_hypothesis[key]
    n = len(sessions)

    def bucket(counter):
        ranges = [("0", 0, 0), ("1", 1, 1), ("2-3", 2, 3), ("4-10", 4, 10), ("11-30", 11, 30), ("31+", 31, 99)]
        return [(label, sum(c for size, c in counter.items() if low <= size <= high)) for label, low, high in ranges]
    r.p(md_table(["Candidate civs", f"A: all {len(all_civs)} civs", "Share",
                  f"B: {len(known_2023)} civs of the 2023 game", "Share",
                  "C: B plus the camel hypothesis", "Share"],
                 [(a[0], a[1], pct(a[1], n), b[1], pct(b[1], n), c[1], pct(c[1], n))
                  for a, b, c in zip(bucket(sizes_all), bucket(sizes_2023), bucket(sizes_camel))]))
    r.p(f"Sessions whose only candidate is Gurjaras: {only_candidate['Gurjaras']} in B, "
        f"{only_candidate_camel['Gurjaras']} in C.")

    r.h(3, "8.0 Camel hypothesis")
    queued = con.execute("""SELECT COUNT(DISTINCT session) FROM ev
        WHERE action='QUEUE' AND phase='COMMAND' AND name='Camel Scout'""").fetchone()[0]
    initial = con.execute("""SELECT COUNT(DISTINCT session) FROM ev
        WHERE action='CREATE_INITIAL' AND name='Camel Scout'""").fetchone()[0]
    has_rider_type = con.execute(
        "SELECT COUNT(*) FROM ev WHERE action='QUEUE' AND name='Camel Rider'").fetchone()[0]
    upgrade_events = con.execute(
        "SELECT COUNT(*) FROM ev WHERE action='RESEARCH' AND phase='COMMAND' AND name='Heavy Camel Rider'").fetchone()[0]
    camel_delta = pair_deltas(con, "QUEUE").get("Camel Scout", collections.Counter()).most_common(1)
    r.p(f"The tree gives `Camel Scout` (Unit {CAMEL_SCOUT_ID}, train time "
        f"{data['data']['Unit'][str(CAMEL_SCOUT_ID)]['TrainTime']} s) to "
        f"{', '.join(sorted(n for n in data['civs'] if CAMEL_SCOUT_ID in civ_available[n]['Unit']))} only. "
        f"In the log, {queued} sessions queue it and {initial} start with one. Queue events named "
        f"`Camel Rider`: {has_rider_type}, although `Heavy Camel Rider` is researched "
        f"{upgrade_events} times. Log duration: {camel_delta[0][0] if camel_delta else 'n/a'} s; "
        f"Camel Rider (Unit {CAMEL_RIDER_ID}) train time in data.json: "
        f"{data['data']['Unit'][str(CAMEL_RIDER_ID)]['TrainTime']} s. Hypothesis: the log labels "
        "ordinary Camel Riders as `Camel Scout`. Equal train times cannot tell the two apart, so "
        "the evidence is how many sessions use the unit and the missing `Camel Rider` type. "
        "Scenario C counts civs with either unit. This is not confirmed and needs a decision for "
        "`manual_mapping.csv`.")
    if zero_causes:
        r.p("Sessions with no candidate in scenario C. The named action is the first one, going "
            "from rarest to most common, that empties the candidate set. It is a pointer, not "
            "proof of a single cause: the tree may have changed since 2023, a player may have "
            "converted a building, or the log may mislabel more units.")
        r.p(md_table(["Normalized name", "Sessions"],
                     sorted(zero_causes.items(), key=lambda kv: (-kv[1], kv[0]))[:25]))

    r.h(3, "8.1 Actions that no 2023 civ has according to this tree")
    rows = []
    for (action, norm), civs in sorted(civs_for.items()):
        if not civs & known_2023:
            count = sum((action, norm) in u for u in used.values())
            rows.append((action, norm, count, ", ".join(sorted(civs)) or "(none)"))
    r.p(md_table(["Action", "Name", "Sessions", "Civs that have it"], rows) if rows else "None.")

    r.h(3, "8.2 Starting-unit signal")
    r.p("Starting units are not in the civ lists but could add evidence in Phase 4. This shows only "
        "how often each signal occurs:")
    both = collections.Counter()
    per_match = collections.Counter()
    for name, match_id, count in con.execute("""
            SELECT name, match_id, COUNT(DISTINCT session) FROM ev WHERE action='CREATE_INITIAL'
            AND name IN ('Llama','Eagle Scout','Camel Scout','Bactrian Camel','Scout Cavalry')
            GROUP BY name, match_id"""):
        per_match[name] += count
        both[name] += count == 2
    r.p("A unit that both players start with in the same match more likely depends on the map "
        "than on the civ.")
    r.p(md_table(["Initial object", "Sessions", "Matches where both players have it"],
                 [(name, count, both[name]) for name, count in
                  sorted(per_match.items(), key=lambda kv: (-kv[1], kv[0]))]))

    r.h(3, "8.3 Evidence for civs that are newer than the replays")
    log_names = {normalize(name) for (name,) in con.execute("SELECT DISTINCT name FROM ev")}
    tree_rows = []
    for civ in sorted(data["civs"]):
        tree = json.loads((TECHTREE_DIR / f"trees/{civ.upper()}.json").read_text())
        unique = {x["name"] for x in tree["buildings"] + tree["units_techs"]
                  if x["node_type"] in ("UniqueUnit", "UniqueTech") and x["node_status"] != "NotAvailable"}
        seen = sorted(x for x in unique if normalize(x) in log_names)
        missing = sorted(x for x in unique if normalize(x) not in log_names)
        if civ in ASSUMED_POST_REPLAY_CIVS or not seen:
            tree_rows.append((civ, "yes" if civ in ASSUMED_POST_REPLAY_CIVS else "",
                              len(unique), ", ".join(seen) or "-", ", ".join(missing)))
    r.p("A civ's unique units and techs appear in the log only if it existed then. The list holds "
        "civs assumed to be newer than the replays, and any civ with no unique item in the log. "
        "The assumption itself is a constant in this script and comes from outside the data.")
    r.p(md_table(["Civ", "Assumed newer", "Unique items", "Seen in log", "Not in log"], tree_rows))

    r.h(3, "8.4 Trainable units that the log never queues")
    queued_names = {normalize(name) for (name,) in con.execute(
        "SELECT DISTINCT name FROM ev WHERE action='QUEUE'")}
    upgrades = data["data"]["unit_upgrades"]
    never = sorted(
        (node["name"], ", ".join(sorted(node["civs_available"] & known_2023)))
        for key, node in nodes.items()
        if key[0] == "Unit" and str(key[1]) not in upgrades
        and node["node_types"] & UNIT_NODE_TYPES and node["civs_available"] & known_2023
        and not any(normalize(x) in queued_names for x in node["names"]))
    r.p("Units that are not upgrades and that a 2023 civ can have according to this tree, with no "
        "QUEUE event of that name. An absent unit may be mislabeled, dropped by the log, simply "
        "unused in these matches, or newer than the replays.")
    r.p(md_table(["Unit", "2023 civs with it"], never))


# ---------------------------------------------------------------------------------------------
# Section 9: 2023 vs 2026 rules. Both versions go through the same matching rule here, so the
# differences come from the data and not from the method.

CATEGORY_RULE = {"QUEUE": lambda n: n["category"] == "Unit",
                 "RESEARCH": lambda n: n["category"] == "Tech" or n["is_upgrade"],
                 "BUILD": lambda n: n["category"] == "Building"}
COMPARE_ITEMS = ["Supplies", "Gambesons", "Bearded Axe", "Pavise", "Scutage", "Hauberk", "Hulk",
                 "Trade Cog", "Trebuchet", "Camel Scout", "Camel Rider", "Eagle Scout"]


def category_index(rules):
    """action -> normalized name -> nodes available to at least one civ."""
    index = {action: collections.defaultdict(list) for action in CATEGORY_RULE}
    for key in sorted(rules.nodes):
        node = rules.nodes[key]
        if not node["civs_available"]:
            continue
        for action, accepts in CATEGORY_RULE.items():
            if accepts(node):
                for name in sorted(node["names"]):
                    index[action][normalize(name)].append(node)
    return index


def log_names(con):
    """Per action: {name: number of decision events} (COMMAND, or START for BUILD)."""
    result = {}
    for action in CATEGORY_RULE:
        result[action] = dict(con.execute("""SELECT name, COUNT(*) FROM ev WHERE action=? AND
            phase=(CASE ? WHEN 'BUILD' THEN 'START' ELSE 'COMMAND' END) GROUP BY name""", (action, action)))
    return result


def match_rows(names, index):
    rows, unmatched = [], {}
    for action, weights in names.items():
        display = {n for nodes in index[action].values() for node in nodes for n in node["names"]}
        exact = [n for n in weights if n in display]
        normed = [n for n in weights if n and normalize(n) in index[action]]
        unmatched[action] = sorted(n for n in weights if n not in normed)
        total = sum(weights.values())
        rows.append((len(weights), f"{len(exact)} ({pct(len(exact), len(weights))})",
                     f"{len(normed)} ({pct(len(normed), len(weights))})",
                     pct(sum(weights[n] for n in exact), total), pct(sum(weights[n] for n in normed), total)))
    return rows, unmatched


def node_times(index, action, name):
    times = set()
    for node in index[action].get(normalize(name), []):
        value = node["upgrade_time"] if action == "RESEARCH" and node["category"] == "Unit" else node["time"]
        if value is not None:
            times.add(value)
    return times


def narrowing(con, rules, index, civs):
    """Candidate-set sizes per session, with and without the camel hypothesis."""
    used = collections.defaultdict(set)
    for session, action, name in con.execute("""SELECT DISTINCT session, action, name FROM ev
            WHERE (action IN ('QUEUE','RESEARCH') AND phase='COMMAND') OR (action='BUILD' AND phase='START')"""):
        if index[action].get(normalize(name)):
            used[session].add((action, normalize(name)))
    civs = set(civs)
    available = {}
    for items in used.values():
        for key in items:
            if key not in available:
                available[key] = {c for c in civs if any(
                    n["id"] in rules.civ_available[c][n["category"]] for n in index[key[0]][key[1]])}
    camel_key = ("QUEUE", normalize("Camel Scout"))
    riders = {c for c in civs if CAMEL_RIDER_ID in rules.civ_available[c]["Unit"]}
    with_camel = dict(available)
    if camel_key in with_camel:
        with_camel[camel_key] = available[camel_key] | riders
    sizes, sizes_camel = collections.Counter(), collections.Counter()
    gurjaras = [0, 0]
    zero_causes = collections.Counter()
    for (session,) in con.execute("SELECT id FROM ob WHERE type='Session' ORDER BY id"):
        plain, camel = set(civs), set(civs)
        for key in used[session]:
            plain &= available[key]
            camel &= with_camel[key]
        sizes[len(plain)] += 1
        sizes_camel[len(camel)] += 1
        gurjaras[0] += plain == {"Gurjaras"}
        gurjaras[1] += camel == {"Gurjaras"}
        if not camel:
            remaining = set(civs)
            for key in sorted(used[session], key=lambda k: (len(with_camel[k]), k)):
                if not remaining & with_camel[key]:
                    zero_causes[key[1]] += 1
                    break
                remaining &= with_camel[key]
    return sizes, sizes_camel, gurjaras, zero_causes


def section_compare(con, r, old, new):
    r.h(2, "9. Tree data 2023 vs 2026")
    r.p("Primary rules: aoe2techtree at commit "
        f"[`{TECHTREE_2023_COMMIT[:10]}`](https://github.com/SiegeEngineers/aoe2techtree/tree/{TECHTREE_2023_COMMIT}) "
        "(2023-12-15, last commit of 2023, the day after the last replay), stored in "
        "`data/raw/aoe2techtree_2023/`. The 2026 snapshot from sections 6 to 8 is the fallback.")

    r.h(3, "9.1 Files and fields")
    r.p(md_table(["File at the 2023 commit", "Present", "Provides"], [
        ("data/data.json", "yes", "costs, train and research times, internal names, `techtrees` (per-civ "
         "lists of buildings, techs and units with an age each, plus the civ's unique unit and techs)"),
        ("data/locales/en/strings.json", "yes", "display names; `LanguageNameId` resolves directly"),
        ("data/trees/<CIV>.json", "**no**", "did not exist yet; the 2026 site uses these"),
        ("js/techtree.js", "yes", "the tree layout: lanes (which building produces what) and "
         "`getConnections()` (the drawn prerequisite links), parsed with regular expressions"),
    ]))
    def field_counts(rules):
        nodes = [n for n in rules.nodes.values() if n["civs_available"]]
        made = [n for n in nodes if n["category"] != "Building"]
        resolved = sum(1 for n in nodes if n["name"] != n["internal_name"] or n["category"] == "Tech")
        return [len(nodes), sum(n["name"] is not None for n in nodes),
                sum(bool(n["cost"]) or bool(n["upgrade_cost"]) for n in nodes),
                sum(n["time"] is not None for n in nodes), sum(bool(n["age_by_civ"]) for n in nodes),
                f"{sum(bool(n['producers']) for n in made)}/{len(made)}",
                sum(bool(n["prereqs"]) for n in nodes)]
    labels = ["Nodes available to some civ", "with a name", "with a cost", "with a train/research time",
              "with a per-civ age", "units and techs with a producer", "with a drawn prerequisite link"]
    old_counts, new_counts = field_counts(old), field_counts(new)
    r.p(md_table(["Field", "2023", "2026"], [(l, a, b) for l, a, b in zip(labels, old_counts, new_counts)]))
    r.p("The 2023 files have every field the schema needs, so the 2026 fallback is needed only for "
        "objects that are missing from 2023 altogether (section 9.4).")

    r.h(3, "9.2 Civilizations")
    only_old = sorted(set(old.civs) - set(new.civs))
    only_new = sorted(set(new.civs) - set(old.civs))
    r.p(f"2023: {len(old.civs)} civs. 2026: {len(new.civs)} civs. Only in 2026: "
        f"{', '.join(only_new) or 'none'}. Only in 2023: {', '.join(only_old) or 'none'}.")
    r.p("The assumption in section 8 (`ASSUMED_POST_REPLAY_CIVS`) " + (
        "matches the 2026-only list exactly." if set(only_new) == ASSUMED_POST_REPLAY_CIVS else
        f"differs from it: assumed {sorted(ASSUMED_POST_REPLAY_CIVS)}."))

    r.h(3, "9.3 Selected objects")
    names = log_names(con)
    log_count = collections.Counter()
    for weights in names.values():
        for name, n in weights.items():
            log_count[normalize(name)] += n
    def describe(rules, item):
        hits = [n for n in rules.nodes.values() if item in n["names"] and n["civs_available"]]
        return "; ".join(f"{n['category']} {n['id']}, {len(n['civs_available'])} civs" for n in hits) or "**missing**"
    r.p(md_table(["Name", "2023", "2026", "Decision events in log"],
                 [(item, describe(old, item), describe(new, item), f"{log_count[normalize(item)]:,}")
                  for item in COMPARE_ITEMS]))
    scutage = old.constants.get("SCUTAGE")
    if scutage:
        r.p(f"`Scutage` has no name match in either version, but the 2023 `techtree.js` declares "
            f"`const SCUTAGE = {scutage}`, and tech {scutage} is called "
            f"\"{old.nodes[('Tech', scutage)]['name']}\" in the 2023 string table. So the log uses the "
            "tech's older name. This is a `manual_mapping.csv` candidate with the constant as evidence.")

    camel_delta = pair_deltas(con, "QUEUE").get("Camel Scout", collections.Counter()).most_common(1)
    scout, rider = old.nodes[("Unit", CAMEL_SCOUT_ID)], old.nodes[("Unit", CAMEL_RIDER_ID)]
    r.p(f"Camel hypothesis (section 8.0) with 2023 values: Camel Scout trains in {scout['time']} s, "
        f"Camel Rider in {rider['time']} s, and the log's `Camel Scout` always takes "
        f"{camel_delta[0][0] if camel_delta else 'n/a'} s. In 2026 both take "
        f"{new.nodes[('Unit', CAMEL_SCOUT_ID)]['time']} and {new.nodes[('Unit', CAMEL_RIDER_ID)]['time']} s. "
        "The 2023 duration therefore points to Camel Rider.")

    r.h(3, "9.4 Name matching against the log")
    r.p("Rule for both versions: QUEUE matches units, RESEARCH matches techs and unit upgrades, BUILD "
        "matches buildings, considering only nodes that some civ has. This is broader than the node-type "
        "rule of section 7, so the 2026 numbers can differ slightly from there.")
    index_old, index_new = category_index(old), category_index(new)
    rows_old, unmatched_old = match_rows(names, index_old)
    rows_new, unmatched_new = match_rows(names, index_new)
    headers = ["Names", "Exact", "Normalized", "Events, exact", "Events, normalized"]
    r.p(md_table(["Source", "Version"] + headers,
                 [row for action, a, b in zip(CATEGORY_RULE, rows_old, rows_new)
                  for row in ((action, "2023") + a, (action, "2026") + b)]))
    rows = []
    for action in CATEGORY_RULE:
        for name in sorted(set(unmatched_old[action]) | set(unmatched_new[action])):
            rows.append((action, show(name), f"{names[action][name]:,}",
                         "no" if name in unmatched_old[action] else "yes",
                         "no" if name in unmatched_new[action] else "yes"))
    r.p("Names unmatched in at least one version:")
    r.p(md_table(["Source", "Log name", "Decision events", "Matched in 2023", "Matched in 2026"], rows))
    fallback = [(a, n) for a in CATEGORY_RULE for n in unmatched_old[a] if n not in unmatched_new[a]]
    r.p("Names that would need a 2026 fallback row (matched in 2026, not in 2023): " +
        (", ".join(f"{a} `{n}`" for a, n in fallback) or "none") + ".")

    r.h(3, "9.5 Fixed durations against the rule files")
    r.p("The log's constant Start-to-Complete duration per type (section 3.1) against the train or "
        "research time of all candidate objects. BUILD compares the longest observed duration.")
    rows = []
    changed = []
    for action in ("QUEUE", "RESEARCH"):
        deltas = pair_deltas(con, action)
        counts = {"2023": [0, 0, 0], "2026": [0, 0, 0]}
        for name, counter in deltas.items():
            mode = counter.most_common(1)[0][0]
            results = {}
            for version, index in (("2023", index_old), ("2026", index_new)):
                times = node_times(index, action, name)
                if times:
                    counts[version][0] += 1
                    counts[version][1] += mode in times
                    counts[version][2] += mode == 0
                    results[version] = mode in times
            if results.get("2023") != results.get("2026"):
                changed.append((action, show(name), mode,
                                ", ".join(map(str, sorted(node_times(index_old, action, name)))) or "-",
                                ", ".join(map(str, sorted(node_times(index_new, action, name)))) or "-"))
        for version in ("2023", "2026"):
            mapped, equal, zero = counts[version]
            rows.append((action, version, mapped, f"{equal} ({pct(equal, mapped)})", zero))
    build = collections.defaultdict(int)
    for name, delta in con.execute("""
            SELECT ev.name, MAX(CASE WHEN ev.phase='COMPLETE' THEN ev.ts END)
                          - MIN(CASE WHEN ev.phase='START' THEN ev.ts END)
            FROM ev JOIN eo ON eo.id = ev.id AND eo.otype = ev.name
            WHERE ev.action='BUILD' GROUP BY eo.oid"""):
        build[name] = max(build[name], delta)
    for version, index in (("2023", index_old), ("2026", index_new)):
        mapped = [n for n in build if node_times(index, "BUILD", n)]
        equal = sum(build[n] in node_times(index, "BUILD", n) for n in mapped)
        rows.append(("BUILD (longest)", version, len(mapped), f"{equal} ({pct(equal, len(mapped))})",
                     sum(build[n] == 0 for n in mapped)))
    r.p(md_table(["Action", "Version", "Mapped types", "Log duration equals a rule time",
                  "Of these types, with log duration 0 s"], rows))
    r.p(fold(f"{len(changed)} QUEUE/RESEARCH types whose match changes between versions",
             md_table(["Action", "Type", "Log duration (s)", "2023 times", "2026 times"], changed)))

    r.h(3, "9.6 Civ narrowing preview")
    new_old_civs = [c for c in new.civs if c not in ASSUMED_POST_REPLAY_CIVS]
    results = {
        "2023": narrowing(con, old, index_old, old.civs),
        "2026": narrowing(con, new, index_new, new_old_civs),
    }
    def bucket(counter):
        ranges = [("0", 0, 0), ("1", 1, 1), ("2-3", 2, 3), ("4-10", 4, 10), ("11-30", 11, 30), ("31+", 31, 99)]
        return [sum(c for size, c in counter.items() if low <= size <= high) for _, low, high in ranges]
    n = sum(results["2023"][0].values())
    columns = [("2023", 0), ("2023 + camel", 1), (f"2026, {len(new_old_civs)} civs", 0),
               (f"2026, {len(new_old_civs)} civs + camel", 1)]
    buckets = [bucket(results["2023" if label.startswith("2023") else "2026"][i]) for label, i in columns]
    labels = ["0", "1", "2-3", "4-10", "11-30", "31+"]
    r.p("Same method as section 8 (normalized names, a civ is a candidate if it has every used object), "
        "with the category rule from 9.4. *+ camel* also accepts Camel Rider for the log's "
        "`Camel Scout` (section 8.0).")
    r.p(md_table(["Candidate civs"] + [label for label, _ in columns],
                 [(label, *[f"{b[i]} ({pct(b[i], n)})" for b in buckets]) for i, label in enumerate(labels)]))
    g_old, g_new = results["2023"][2], results["2026"][2]
    r.p(f"Sessions whose only candidate is Gurjaras: 2023 {g_old[0]}, 2023 + camel {g_old[1]}, "
        f"2026 {g_new[0]}, 2026 + camel {g_new[1]}.")
    zero = results["2023"][3]
    if zero:
        r.p("2023 + camel, sessions with no candidate. The named action is the first one, from rarest "
            "to most common, that empties the candidate set:")
        r.p(md_table(["Normalized name", "Sessions"], sorted(zero.items(), key=lambda kv: (-kv[1], kv[0]))))

    r.h(3, "9.7 Value drift between versions for objects the log uses")
    rows = []
    for action in CATEGORY_RULE:
        for name in sorted(names[action]):
            a = index_old[action].get(normalize(name), [])
            b = index_new[action].get(normalize(name), [])
            if len(a) != 1 or len(b) != 1:
                continue
            a, b = a[0], b[0]
            upgrade = action == "RESEARCH" and a["category"] == "Unit"
            time_key, cost_key = ("upgrade_time", "upgrade_cost") if upgrade else ("time", "cost")
            for field, va, vb in (("time", a[time_key], b[time_key]), ("cost", a[cost_key], b[cost_key]),
                                  ("min age", min(a["ages"]), min(b["ages"])),
                                  ("civs", len(a["civs_available"]), len(b["civs_available"] - ASSUMED_POST_REPLAY_CIVS))):
                if va != vb:
                    rows.append((action, name, field, va, vb))
    r.p(f"Objects matched to exactly one node in both versions, compared field by field. "
        f"{len(rows)} differences. For RESEARCH of a unit upgrade, *time* and *cost* are the upgrade's "
        "research time and cost. *civs* counts only the 45 civs of 2023.")
    r.p(fold("Differences", md_table(["Action", "Log name", "Field", "2023", "2026"], rows)))


def main():
    parser = argparse.ArgumentParser(description="Profile the OCEL log and tech tree files.")
    parser.add_argument("--ocel", type=Path, default=DEFAULT_OCEL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if not args.ocel.exists():
        sys.exit(f"Missing {args.ocel}")

    started = time.time()
    source_before = args.ocel.stat()
    data, strings, nodes, civ_available = load_techtree()
    name_index = build_name_index(nodes, data)
    con = sqlite3.connect(f"file:{args.ocel}?mode=ro", uri=True)
    build_working_tables(con)

    r = Report()
    r.h(1, "Exploration report (Phase 1)")
    r.p(f"Generated by `scripts/01_explore.py`. Log: `{args.ocel.name}`. Rules: `data/raw/data.json` "
        "and the 2026 snapshot in `data/raw/aoe2techtree/` (sections 6 to 8); section 9 compares them with "
        "the 2023 files in `data/raw/aoe2techtree_2023/`.")
    sections = [
        lambda: section_tables(con, r),
        lambda: section_events(con, r),
        lambda: section_time(con, r, data, name_index),
        lambda: section_objects(con, r),
        lambda: section_attributes(con, r),
        lambda: section_data_json(r, data, strings, nodes, civ_available),
        lambda: section_names(con, r, data, nodes, name_index),
        lambda: section_civ_preview(con, r, data, nodes, civ_available, name_index),
        lambda: section_compare(con, r, load_2023(), load_2026()),
    ]
    for number, section in enumerate(sections, 1):
        section()
        print(f"section {number}/{len(sections)} done at {time.time() - started:.0f}s", flush=True)
    con.close()

    source_after = args.ocel.stat()
    if (source_before.st_mtime_ns, source_before.st_size) != (source_after.st_mtime_ns, source_after.st_size):
        sys.exit("The source file changed during the run. This should be impossible with mode=ro.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(r.render())
    print(f"wrote {args.out} in {time.time() - started:.0f}s; source file unchanged")


if __name__ == "__main__":
    main()
