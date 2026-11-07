#!/usr/bin/env python3
"""Phase 2: create data/processed/aoe2.db from 02_schema.sql and load the static game rules.

Sources, in order of precedence:
  1. data/manual/*.csv            hand-kept decisions with evidence (source 'manual')
  2. data/raw/aoe2techtree_2023/  aoe2techtree at commit 9d2f898 (source '2023')
  3. data/raw/aoe2techtree/ + data/raw/data.json, the 2026 snapshot (source '2026'),
     used only for log names that 2023 cannot map
The OCEL log is opened read-only to collect the object names it uses.

The script applies the whole schema, so it resets the database. Run 04+ again afterwards.
It also writes reports/static_data.md.

Usage: python3 scripts/03_load_static.py [--ocel PATH] [--db PATH]
"""
import argparse
import collections
import csv
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aoe2_rules import (DATA_JSON_2026, DIR_2023, DIR_2026, TECHTREE_2023_COMMIT,  # noqa: E402
                        load_2023, load_2026, normalize, parse_event_type)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data/processed/aoe2.db"
DEFAULT_OCEL = ROOT / "data/raw/age_of_empires_ocel2.sqlite"
SCHEMA = ROOT / "scripts/02_schema.sql"
MANUAL_MAPPING = ROOT / "data/manual/manual_mapping.csv"
MANUAL_DEPENDENCIES = ROOT / "data/manual/manual_dependencies.csv"
MANUAL_CIV_OVERRIDES = ROOT / "data/manual/manual_civ_overrides.csv"
REPORT = ROOT / "reports/static_data.md"

TYPE_OF_CATEGORY = {"Building": "BUILDING", "Unit": "UNIT", "Tech": "TECHNOLOGY"}
VILLAGER, FISHING_SHIP, FISH_TRAP = 83, 13, 199  # tree IDs
# Which game object types a log name can refer to, per action.
ACTION_TYPES = {"QUEUE": ("UNIT",), "RESEARCH": ("TECHNOLOGY",), "BUILD": ("BUILDING",),
                "CREATE_INITIAL": ("UNIT", "BUILDING")}
# Spot check against general AoE2 DE knowledge (Claude's, not verified in the game).
# (object type, object name, required type, required name, expected to be a prerequisite)
SPOT_CHECKS = [
    ("BUILDING", "Archery Range", "BUILDING", "Barracks", True),
    ("BUILDING", "Stable", "BUILDING", "Barracks", True),
    ("BUILDING", "Market", "BUILDING", "Mill", True),
    ("BUILDING", "Siege Workshop", "BUILDING", "Blacksmith", True),
    ("BUILDING", "Farm", "BUILDING", "Mill", True),
    ("TECHNOLOGY", "Heavy Plow", "TECHNOLOGY", "Horse Collar", True),
    ("TECHNOLOGY", "Iron Casting", "TECHNOLOGY", "Forging", True),
    ("TECHNOLOGY", "Arbalester", "TECHNOLOGY", "Crossbowman", True),
    ("TECHNOLOGY", "Hussar", "TECHNOLOGY", "Light Cavalry", True),
    ("TECHNOLOGY", "Town Patrol", "TECHNOLOGY", "Town Watch", True),
    ("BUILDING", "University", None, None, False),
    ("BUILDING", "Blacksmith", None, None, False),
]
# (object type, name, expected required_age, expected producer name)
ATTRIBUTE_CHECKS = [
    ("UNIT", "Villager", 1, "Town Center"),
    ("TECHNOLOGY", "Crossbowman", 3, "Archery Range"),
    ("UNIT", "Monk", 3, "Monastery"),
    ("TECHNOLOGY", "Loom", 1, "Town Center"),
    ("TECHNOLOGY", "Castle Age", 2, "Town Center"),
]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def md_table(headers, rows):
    def cell(value):
        return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


class StaticLoader:
    def __init__(self, con, old, new):
        self.con = con
        self.old = old      # 2023 rules
        self.new = new      # 2026 rules, fallback only
        self.obj = {}       # (category, tree id) -> game_object.id, 2023 nodes
        self.upgrade_tech = {}  # unit tree id -> game_object.id of its upgrade technology
        self.civ = {}       # civ name -> civilization.id
        self.unknown = None
        self.stats = collections.Counter()
        # Buildings that train units directly according to the tree links, per unit tree id.
        self.trained_from = collections.defaultdict(set)
        for a_cat, a, b_cat, b in old.connections:
            if a_cat == "Building" and b_cat == "Unit" and b in old.constants and a in old.constants:
                self.trained_from[old.constants[b]].add(old.constants[a])
        # Replacement buildings: a building that produces an item whose lane producer is
        # another building (Fortified Church for Monastery, Mule Cart for Lumber Camp, ...).
        self.replacements = collections.defaultdict(set)
        for node in old.nodes.values():
            alternatives = {i for c, i in node["prereqs"] if c == "Building"} - node["producers"]
            if node["category"] != "Building":
                for producer in node["producers"]:
                    self.replacements[producer] |= alternatives
        self.notes = collections.defaultdict(list)

    # -- game objects --------------------------------------------------------------------------
    def insert_object(self, type_, tree_id, name, internal_name, time_, cost, source, detail,
                      is_unique=0, upgrades_to=None):
        cost = cost or {}
        return self.con.execute(
            """INSERT INTO game_object (type, tree_id, name, internal_name, time, cost_food, cost_wood,
               cost_gold, cost_stone, upgrades_to, is_unique, source, source_detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (type_, tree_id, name, internal_name, time_, cost.get("food", 0), cost.get("wood", 0),
             cost.get("gold", 0), cost.get("stone", 0), upgrades_to, is_unique, source, detail)).lastrowid

    def load_objects(self):
        tables = {"Building": "buildings", "Unit": "units", "Tech": "techs"}
        for key in sorted(self.old.nodes):
            node = self.old.nodes[key]
            if not node["civs_available"]:
                self.stats["nodes without any civ (skipped)"] += 1
                continue
            category, tree_id = key
            unique = int(bool(node["node_types"] & {"UniqueUnit", "UniqueTech"}))
            self.obj[key] = self.insert_object(
                TYPE_OF_CATEGORY[category], tree_id, node["name"], node["internal_name"], node["time"],
                node["cost"], "2023", f"aoe2techtree_2023/data.json data.{tables[category]}[{tree_id}]", unique)

        upgrades = self.old.data["data"]["unit_upgrades"]
        by_tech = collections.defaultdict(list)
        for (category, tree_id), node in self.old.nodes.items():
            if category == "Unit" and node["is_upgrade"] and (category, tree_id) in self.obj:
                by_tech[upgrades[str(tree_id)]["ID"]].append(tree_id)
        for tech_id in sorted(by_tech):
            units = sorted(by_tech[tech_id], key=lambda u: (-len(self.old.nodes[("Unit", u)]["civs_available"]), u))
            primary = self.old.nodes[("Unit", units[0])]
            upgrade = upgrades[str(units[0])]
            detail = f"aoe2techtree_2023/data.json unit_upgrades[{units[0]}] (tech {tech_id})"
            if len(units) > 1:
                detail += "; the same tech also upgrades unit(s) " + ", ".join(map(str, units[1:]))
            existing = self.con.execute("SELECT id FROM game_object WHERE type='TECHNOLOGY' AND tree_id=?",
                                        (tech_id,)).fetchone()
            if existing:
                tech = existing[0]
                self.con.execute("UPDATE game_object SET upgrades_to=? WHERE id=?", (self.obj[("Unit", units[0])], tech))
            else:
                tech = self.insert_object("TECHNOLOGY", tech_id, primary["name"], upgrade["internal_name"],
                                          primary["upgrade_time"], primary["upgrade_cost"], "2023", detail,
                                          int("UniqueUnit" in primary["node_types"]), self.obj[("Unit", units[0])])
            for unit in units:
                self.upgrade_tech[unit] = tech
        self.stats["upgrade technologies"] = len(by_tech)
        self.unknown = self.insert_object("UNKNOWN", None, "UNKNOWN", None, None, None, "manual",
                                          "placeholder for the log names 'None' and '' (empty suffix)")

    # -- civilizations -----------------------------------------------------------------------------
    def load_civilizations(self):
        for civ in self.old.civs:
            self.civ[civ] = self.con.execute(
                "INSERT INTO civilization (name, source, source_detail) VALUES (?, '2023', ?)",
                (civ, f"aoe2techtree_2023/data.json techtrees[{civ}]")).lastrowid

    def producer_for(self, key, civ):
        """Producer building tree id for this node and civ, plus a note on how it was chosen."""
        category, tree_id = key
        node = self.old.nodes[key]
        if category == "Building":
            return ("Unit", FISHING_SHIP if tree_id == FISH_TRAP else VILLAGER), "builder"
        candidates = set(node["producers"]) | {i for c, i in node["prereqs"] if c == "Building"}
        own = [b for b in candidates if b in self.old.civ_available[civ]["Building"]]
        if own:
            best = min(own, key=lambda b: (self.old.nodes[("Building", b)]["age_by_civ"].get(civ, 9), b))
            return ("Building", best), "lane"
        substitutes = sorted({r for b in candidates for r in self.replacements[b]
                              if r in self.old.civ_available[civ]["Building"]})
        if substitutes:
            self.stats["civ rows produced at a replacement building"] += 1
            return ("Building", substitutes[0]), "replacement building derived from tree links"
        if candidates:
            self.stats["civ rows whose producer the civ lacks"] += 1
            return ("Building", min(candidates)), "lane, building not available to this civ"
        return None, None

    def load_civilization_objects(self):
        rows = []
        for key in sorted(self.obj):
            node = self.old.nodes[key]
            for civ in sorted(node["civs_available"]):
                producer, how = self.producer_for(key, civ)
                producer_id = self.obj.get(producer) if producer else None
                detail = f"age: data.json techtrees[{civ}]; producer: js/techtree.js ({how})"
                rows.append((self.civ[civ], self.obj[key], node["age_by_civ"][civ], producer_id, detail))
                if key[0] == "Unit" and key[1] in self.upgrade_tech:
                    rows.append((self.civ[civ], self.upgrade_tech[key[1]], node["age_by_civ"][civ], producer_id,
                                 detail + "; upgrade research, same age and producer as the unit"))
        self.con.executemany(
            """INSERT OR IGNORE INTO civilization_object (civ_id, object_id, required_age, produced_at, source,
               source_detail) VALUES (?, ?, ?, ?, '2023', ?)""", rows)
        self.apply_civ_overrides()
        # game_object: earliest age, most common producer (ties: lowest id)
        by_object = collections.defaultdict(list)
        for object_id, age, producer in self.con.execute(
                "SELECT object_id, required_age, produced_at FROM civilization_object"):
            by_object[object_id].append((age, producer))
        for object_id, values in by_object.items():
            producers = collections.Counter(p for _, p in values if p is not None)
            producer = min(producers, key=lambda p: (-producers[p], p)) if producers else None
            self.con.execute("UPDATE game_object SET required_age=?, produced_at=? WHERE id=?",
                             (min(a for a, _ in values), producer, object_id))

    def apply_civ_overrides(self):
        """Per-civ ages that the 2023 tree gets wrong, from manual_civ_overrides.csv."""
        with open(MANUAL_CIV_OVERRIDES, newline="") as handle:
            for row in csv.DictReader(handle):
                object_id = self.find_object(row["object_type"], None, row["tree_id"])
                updated = self.con.execute(
                    """UPDATE civilization_object SET required_age=?, source='manual', source_detail=?
                       WHERE civ_id=? AND object_id=?""",
                    (int(row["required_age"]), "data/manual/manual_civ_overrides.csv: " + row["evidence"],
                     self.civ[row["civ"]], object_id)).rowcount
                if updated != 1:
                    sys.exit(f"manual_civ_overrides.csv: no row for {row['civ']} / {row['object_type']} {row['tree_id']}")
                self.stats["manual civ overrides"] += 1

    # -- dependencies ----------------------------------------------------------------------------
    def resolve_requirement(self, category, tree_id):
        """('tech'|'unit'|'building', game_object.id) for a tree node, or None if no civ has it."""
        key = (category, tree_id)
        if key not in self.obj:
            return None
        if category == "Unit":
            # A researched upgrade has an upgrade tech and no building that trains it directly
            # (Eagle Warrior, Galleon). Battle Elephant has an upgrade entry but the Stable trains it.
            if tree_id in self.upgrade_tech and not self.trained_from[tree_id]:
                return "tech", self.upgrade_tech[tree_id]
            return "unit", self.obj[key]
        return ("building" if category == "Building" else "tech"), self.obj[key]

    def load_dependencies(self):
        constants = self.old.constants
        building_links = collections.defaultdict(list)  # target object -> [(source object, link)]
        for a_cat, a_name, b_cat, b_name in self.old.connections:
            link = f"js/techtree.js getConnections: [{a_name}, {b_name}]"
            if a_name not in constants or b_name not in constants:
                self.stats["links: unique-unit placeholder (skipped)"] += 1
                continue
            a_id, b_id = constants[a_name], constants[b_name]
            a, b = self.resolve_requirement(a_cat, a_id), self.resolve_requirement(b_cat, b_id)
            if a is None or b is None:
                self.stats["links: object that no civ has (skipped)"] += 1
                continue
            if b[0] == "unit":
                self.stats["links: to a trainable unit, i.e. its producer or line (skipped)"] += 1
                continue
            if a[0] == "building" and b[0] != "building":
                self.stats["links: building that produces it (skipped, see produced_at)"] += 1
                continue
            if a[0] == "building":
                building_links[b[1]].append((a[1], link))
                continue
            if a[0] == "unit":
                self.stats["links: from a trainable base unit (skipped, implied by producer)"] += 1
                continue
            if a[1] == b[1]:
                continue
            inserted = self.con.execute(
                """INSERT OR IGNORE INTO dependency (object_id, requires_id, kind, source, source_detail)
                   VALUES (?, ?, 'tree_link', '2023', ?)""", (b[1], a[1], link)).rowcount
            self.stats["links: stored as dependency"] += inserted
        # Building prerequisites: several sources for one building are alternatives
        # (Farm after a Mill, or after a Folwark for Poles), stored as a 1-of-n group.
        for target in sorted(building_links):
            sources = sorted(set(building_links[target]))
            group = ("alternative_buildings", 1) if len({s for s, _ in sources}) > 1 else (None, None)
            for source, link in sources:
                inserted = self.con.execute(
                    """INSERT OR IGNORE INTO dependency (object_id, requires_id, kind, req_group, req_count, source,
                       source_detail) VALUES (?, ?, 'tree_link', ?, ?, '2023', ?)""",
                    (target, source, group[0], group[1], link)).rowcount
                self.stats["links: stored as dependency" + (" (alternatives group)" if group[0] else "")] += inserted

        with open(MANUAL_DEPENDENCIES, newline="") as handle:
            for row in csv.DictReader(handle):
                obj = self.find_object(row["object_type"], row["object_name"])
                req = self.find_object(row["requires_type"], row["requires_name"])
                if obj is None or req is None:
                    self.notes["manual dependency rows whose object does not exist"].append(
                        f"{row['object_name']} requires {row['requires_name']}")
                    continue
                self.con.execute(
                    """INSERT OR REPLACE INTO dependency (object_id, requires_id, kind, req_group, req_count, weight,
                       source, source_detail) VALUES (?, ?, 'manual', ?, ?, ?, 'manual', ?)""",
                    (obj, req, row["req_group"] or None, int(row["req_count"]) if row["req_count"] else None,
                     int(row["weight"] or 1), "data/manual/manual_dependencies.csv: " + row["evidence"]))
                self.stats["manual dependency rows"] += 1

    # -- name mapping ----------------------------------------------------------------------------
    def object_candidates(self, types, name, normalized=False):
        rows = self.con.execute(f"""
            SELECT g.id, g.type, g.tree_id, g.name, COUNT(c.civ_id) AS civs
            FROM game_object g LEFT JOIN civilization_object c ON c.object_id = g.id
            WHERE g.type IN ({','.join('?' * len(types))}) GROUP BY g.id""", types).fetchall()
        if normalized:
            return [r for r in rows if normalize(r[3]) == normalize(name)]
        return [r for r in rows if r[3] == name]

    @staticmethod
    def generic(candidates):
        """The candidate most civs have; ties: lowest tree id. Returns (row, note)."""
        ordered = sorted(candidates, key=lambda r: (-r[4], r[2] if r[2] is not None else 0))
        note = None
        if len(ordered) > 1:
            note = (f"ambiguous: chose tree id {ordered[0][2]} ({ordered[0][4]} civs) over " +
                    ", ".join(f"{r[2]} ({r[4]} civs)" for r in ordered[1:]))
        return ordered[0], note

    def find_object(self, type_, name, tree_id=None):
        if type_ == "UNKNOWN":
            return self.unknown
        if tree_id not in (None, ""):
            row = self.con.execute("SELECT id FROM game_object WHERE type=? AND tree_id=?",
                                   (type_, int(tree_id))).fetchone()
            return row[0] if row else None
        candidates = self.object_candidates((type_,), name)
        return self.generic(candidates)[0][0] if candidates else None

    def fallback_2026(self, action, name):
        """Insert a 2026 node for a name 2023 cannot map. Returns game_object.id or None."""
        category = {"QUEUE": "Unit", "RESEARCH": "Tech", "BUILD": "Building"}.get(action)
        if category is None:
            return None
        for key in sorted(self.new.nodes):
            node = self.new.nodes[key]
            if key[0] == category and node["civs_available"] and normalize(name) in {normalize(n) for n in node["names"]}:
                object_id = self.insert_object(TYPE_OF_CATEGORY[category], key[1], node["name"], node["internal_name"],
                                               node["time"], node["cost"], "2026",
                                               f"aoe2techtree/trees (2026), node {key[0]}_{key[1]}")
                for civ in sorted(node["civs_available"] & set(self.civ)):
                    self.con.execute(
                        """INSERT INTO civilization_object (civ_id, object_id, required_age, source, source_detail)
                           VALUES (?, ?, ?, '2026', 'aoe2techtree/trees (2026)')""",
                        (self.civ[civ], object_id, node["age_by_civ"][civ]))
                self.con.execute("UPDATE game_object SET required_age=? WHERE id=?", (min(node["ages"]), object_id))
                return object_id
        return None

    def load_name_mapping(self, log_names):
        decided = set()
        with open(MANUAL_MAPPING, newline="") as handle:
            for row in csv.DictReader(handle):
                action, name = row["action"], row["ocel_name"]
                target = None
                if row["target_type"]:
                    target = self.find_object(row["target_type"], row["target_name"], row["target_tree_id"])
                    if target is None:
                        sys.exit(f"manual_mapping.csv: no {row['target_type']} named {row['target_name']!r}")
                if name not in log_names.get(action, {}):
                    self.notes["manual mapping rows for names the log does not use"].append(f"{action} {name!r}")
                self.con.execute("INSERT INTO name_mapping VALUES (?, ?, ?, 'manual', ?)",
                                 (action, name, target, row["evidence"]))
                decided.add((action, name))
        for action in sorted(log_names):
            for name in sorted(log_names[action]):
                if (action, name) in decided:
                    continue
                method, candidates = "exact", self.object_candidates(ACTION_TYPES[action], name)
                if not candidates and name:
                    method, candidates = "normalized", self.object_candidates(ACTION_TYPES[action], name, True)
                note, target = None, None
                if candidates:
                    row, note = self.generic(candidates)
                    target = row[0]
                elif name:
                    target = self.fallback_2026(action, name)
                    method, note = ("exact", "matched in the 2026 fallback only") if target else (None, None)
                else:
                    method = None
                self.con.execute("INSERT INTO name_mapping VALUES (?, ?, ?, ?, ?)", (action, name, target, method, note))


def read_log_names(ocel):
    """{action: {name: decision events}} for QUEUE, RESEARCH, BUILD and CREATE_INITIAL."""
    con = sqlite3.connect(f"file:{ocel}?mode=ro", uri=True)
    names = collections.defaultdict(dict)
    for ocel_type, type_map in con.execute("SELECT ocel_type, ocel_type_map FROM event_map_type ORDER BY 1"):
        parsed = parse_event_type(ocel_type)
        if not parsed or parsed[0] not in ACTION_TYPES:
            continue
        action, phase, name = parsed
        names[action].setdefault(name, 0)
        decision = phase == ("START" if action == "BUILD" else "COMMAND") or action == "CREATE_INITIAL"
        if decision:
            names[action][name] += con.execute(f'SELECT COUNT(*) FROM "event_{type_map}"').fetchone()[0]
    con.close()
    return names


def record_sources(con, ocel):
    rows = [(f"aoe2techtree_2023/{p.name}", "2023", sha256(p),
             f"github.com/SiegeEngineers/aoe2techtree@{TECHTREE_2023_COMMIT}")
            for p in sorted(DIR_2023.glob("*")) if p.name != "manifest.json"]
    rows.append(("data.json", "2026", sha256(DATA_JSON_2026), "aoe2techtree.net/data/data.json"))
    rows += [(f"aoe2techtree/{p.relative_to(DIR_2026)}", "2026", sha256(p), "aoe2techtree.net")
             for p in sorted(DIR_2026.rglob("*.json")) if p.name != "manifest.json"]
    rows += [(f"manual/{p.name}", "manual", sha256(p), "hand-kept")
             for p in (MANUAL_MAPPING, MANUAL_DEPENDENCIES, MANUAL_CIV_OVERRIDES)]
    rows.append((ocel.name, "ocel", sha256(ocel), "zenodo.org/records/13365584"))
    con.executemany("INSERT INTO source_file VALUES (?, ?, ?, ?)", rows)


def write_report(con, loader, log_names, runtime):
    out = ["# Static data load (Phase 2)",
           f"Generated by `scripts/03_load_static.py` in {runtime:.0f} s. Primary rules: aoe2techtree at commit "
           f"`{TECHTREE_2023_COMMIT}`; 2026 only as fallback. Manual decisions: `data/manual/`."]

    out.append("## Row counts")
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out.append(md_table(["Table", "Rows", "Filled by"], [
        (t, f"{con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:,}",
         "04_load_replays.py" if t in ("match", "player", "session", "event") else "this script") for t in tables]))
    out.append(md_table(["game_object type", "source", "Rows"], con.execute(
        "SELECT type, source, COUNT(*) FROM game_object GROUP BY 1, 2 ORDER BY 1, 2").fetchall()))
    out.append(md_table(["civilization_object source", "Rows"], con.execute(
        "SELECT source, COUNT(*) FROM civilization_object GROUP BY 1").fetchall()))
    out.append(md_table(["dependency kind", "Rows"], con.execute(
        "SELECT kind, COUNT(*) FROM dependency GROUP BY 1").fetchall()))
    out.append("How the 2023 tree links were used:\n\n" + md_table(
        ["Link", "Count"], sorted((k, v) for k, v in loader.stats.items() if k.startswith("links"))))
    other = sorted((k, v) for k, v in loader.stats.items() if not k.startswith("links"))
    out.append(md_table(["Other counter", "Value"], other))

    out.append("## Mapping coverage")
    out.append("Decision events: COMMAND for QUEUE and RESEARCH, START for BUILD; all CREATE_INITIAL events.")
    rows = []
    for action in ("QUEUE", "RESEARCH", "BUILD", "CREATE_INITIAL"):
        mapping = {name: (obj, method) for name, obj, method in con.execute(
            "SELECT ocel_name, game_object_id, method FROM name_mapping WHERE action=?", (action,))}
        weights = log_names[action]
        total = sum(weights.values())
        def share(test):
            names = [n for n in weights if test(*mapping.get(n, (None, None)))]
            return f"{len(names)} / {pct(sum(weights[n] for n in names), total)}"
        rows.append((action, len(weights),
                     share(lambda o, m: m == "exact"), share(lambda o, m: m == "normalized"),
                     share(lambda o, m: m == "manual" and o not in (None, loader.unknown)),
                     share(lambda o, m: o == loader.unknown),
                     share(lambda o, m: m == "manual" and o is None),
                     share(lambda o, m: m is None)))
    out.append(md_table(["Action", "Names", "exact", "normalized", "manual", "UNKNOWN",
                         "not a game object", "open"], rows))
    out.append("Each cell: names / share of decision events.")
    ambiguous = con.execute("""SELECT n.action, n.ocel_name, g.type, g.tree_id, n.note FROM name_mapping n
        JOIN game_object g ON g.id = n.game_object_id WHERE n.note LIKE 'ambiguous%' ORDER BY 1, 2""").fetchall()
    out.append("Ambiguous names, resolved to the generic ID (most civs, then lowest tree id):\n\n" +
               md_table(["Action", "Log name", "Type", "Chosen tree id", "Note"], ambiguous))
    fallback = con.execute("SELECT name, type, tree_id FROM game_object WHERE source='2026'").fetchall()
    out.append("2026 fallback objects: " + (", ".join(f"{n} ({t} {i})" for n, t, i in fallback) or "none") + ".")

    out.append("## Open manual mappings")
    open_rows = con.execute("SELECT action, ocel_name FROM name_mapping WHERE method IS NULL ORDER BY 1, 2").fetchall()
    out.append(md_table(["Action", "Log name", "Decision events"],
                        [(a, n or "(empty)", f"{log_names[a].get(n, 0):,}") for a, n in open_rows]) if open_rows
               else "None: every log name is mapped or explicitly decided.")
    out.append("Manual decisions in use:\n\n" + md_table(["Action", "Log name", "Target", "Evidence"], con.execute(
        """SELECT n.action, CASE n.ocel_name WHEN '' THEN '(empty)' ELSE n.ocel_name END,
                  COALESCE(g.type || ' ' || g.name, '(not a game object)'), n.note
           FROM name_mapping n LEFT JOIN game_object g ON g.id = n.game_object_id
           WHERE n.method='manual' ORDER BY 1, 2""").fetchall()))
    for title, items in sorted(loader.notes.items()):
        out.append(f"{title.capitalize()}: " + ", ".join(items) + ".")

    out.append("## Town Center")
    out.append("The 2023 tree has three Town Center buildings. Per-civ ages from `civilization_object`:")
    rows = []
    for tree_id in (109, 621):
        obj = loader.find_object("BUILDING", None, tree_id)
        g = con.execute("SELECT internal_name, time, required_age FROM game_object WHERE id=?", (obj,)).fetchone()
        ages = con.execute("""SELECT required_age, COUNT(*), group_concat(CASE WHEN co.source='manual' THEN v.name END)
            FROM civilization_object co JOIN civilization v ON v.id = co.civ_id WHERE object_id=? GROUP BY 1""",
                           (obj,)).fetchall()
        mapped = ", ".join(f"{a} `{n}`" for a, n in con.execute(
            "SELECT action, ocel_name FROM name_mapping WHERE game_object_id=? ORDER BY 1", (obj,)))
        rows.append((tree_id, g[0], g[1], "; ".join(f"age {a}: {n} civs" + (f" (manual: {m})" if m else "")
                                                     for a, n, m in ages), mapped or "-"))
    rows.append((71, "RTWC2", 100, "no civ has it", "-"))
    out.append(md_table(["Tree id", "internal_name", "Build time (s)", "Per-civ age", "Log names mapped to it"], rows))
    out.append("109 is the Town Center of the Dark Age lane in the 2023 `js/techtree.js` (`TOWN_CENTER`), the TC a "
               "player starts with. 621 (`TOWN_CENTER_2`) is the additional TC in the Castle Age lane. Building a TC "
               "maps to 621, so plausibility checks must use the per-civ age from `civilization_object` (Cumans: "
               "Feudal). Exception: on maps without a starting TC the first TC is built in the Dark Age.")
    out.append("**Population:** neither the 2023 nor the 2026 data.json has population cost or provided population. "
               "No `pop_cost` or `pop_provided` columns were added; this is a known limitation.")

    out.append("## Prerequisite spot check")
    out.append("Expected values are general AoE2 DE knowledge (Claude's), not checked in the game.")
    rows = []
    for o_type, o_name, r_type, r_name, expected in SPOT_CHECKS:
        obj = loader.find_object(o_type, o_name)
        if r_name:
            req = loader.find_object(r_type, r_name)
            stored = con.execute("SELECT kind, source_detail FROM dependency WHERE object_id=? AND requires_id=?",
                                 (obj, req)).fetchone()
            claim = f"{o_name} requires {r_name}"
            found = stored is not None
            detail = stored[1] if stored else "-"
        else:
            reqs = [r[0] for r in con.execute("""SELECT g.name FROM dependency d JOIN game_object g
                ON g.id = d.requires_id WHERE d.object_id=? AND d.req_group IS NULL""", (obj,))]
            claim = f"{o_name} has no building or tech prerequisite (age only)"
            found = bool(reqs)
            detail = ", ".join(reqs) or "none stored"
        rows.append((claim, "yes" if expected else "no", "agrees" if found == expected else "**DISAGREES**", detail))
    out.append(md_table(["Rule", "Expected prerequisite", "Database", "Stored as"], rows))
    rows = []
    for o_type, name, age, producer in ATTRIBUTE_CHECKS:
        got = con.execute("""SELECT g.required_age, p.name FROM game_object g LEFT JOIN game_object p
            ON p.id = g.produced_at WHERE g.id=?""", (loader.find_object(o_type, name),)).fetchone()
        rows.append((f"{name} ({o_type})", f"age {age}, at {producer}", f"age {got[0]}, at {got[1]}",
                     "agrees" if (got[0], got[1]) == (age, producer) else "**DISAGREES**"))
    out.append(md_table(["Object", "Expected", "Database", "Result"], rows))
    groups = con.execute("""SELECT o.name, d.req_group, d.req_count, group_concat(r.name || CASE d.weight WHEN 1 THEN ''
        ELSE ' (weight ' || d.weight || ')' END, ', ') FROM dependency d JOIN game_object o ON o.id = d.object_id
        JOIN game_object r ON r.id = d.requires_id WHERE d.req_group IS NOT NULL GROUP BY 1, 2 ORDER BY 1""").fetchall()
    out.append("Group rules from `manual_dependencies.csv`:\n\n" + md_table(["Object", "Group", "Needed", "Members"], groups))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n\n".join(out) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Create aoe2.db and load the static game rules.")
    parser.add_argument("--ocel", type=Path, default=DEFAULT_OCEL)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if not args.ocel.exists():
        sys.exit(f"Missing {args.ocel}")
    started = time.time()

    old, new = load_2023(), load_2026()
    log_names = read_log_names(args.ocel)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text())
    with con:
        record_sources(con, args.ocel)
        loader = StaticLoader(con, old, new)
        loader.load_objects()
        loader.load_civilizations()
        loader.load_civilization_objects()
        loader.load_dependencies()
        loader.load_name_mapping(log_names)
    problems = con.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        sys.exit(f"Foreign key violations: {problems[:5]}")
    write_report(con, loader, log_names, time.time() - started)
    con.close()
    print(f"wrote {args.db} and {REPORT} in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
