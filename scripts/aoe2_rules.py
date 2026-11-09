"""Load the aoe2techtree rule files of one version into one common shape.

Two versions are available (see scripts/00_fetch_techtree.py):

* 2023: aoe2techtree at commit TECHTREE_2023_COMMIT (2023-12-15), the state at the end of
  the replay period. Costs, times, names and per-civ ages come from data.json and the
  string table. Producers and prerequisite links come from the tree layout hard-coded in
  js/techtree.js, which is parsed with regular expressions and never executed.
* 2026: the live site as of the snapshot in data/raw/aoe2techtree/. Ages, producers and
  links come from its per-civ tree files.

Both loaders return a Rules object whose `nodes` map (category, id) to a dict with the
same keys, so callers do not need to know which version they hold.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
TECHTREE_2023_COMMIT = "9d2f8987b2852695e997cc8ca4bee077108d1fbc"
DIR_2023 = ROOT / "data/raw/aoe2techtree_2023"
DIR_2026 = ROOT / "data/raw/aoe2techtree"
DATA_JSON_2026 = ROOT / "data/raw/data.json"

CATEGORY_OF_2026_LINK = {
    "BuildingTech": "Building", "BuildingNonTech": "Building", "UniqueBuilding": "Building",
    "RegionalBuilding": "Building", "Unit": "Unit", "UniqueUnit": "Unit", "RegionalUnit": "Unit",
    "UnitUpgrade": "Unit", "Research": "Tech", "UniqueTech": "Tech", "RegionalTech": "Tech",
}
CATEGORY_OF_JS_CALL = {"building": "Building", "b": "Building", "unit": "Unit", "uniqueunit": "Unit",
                       "u": "Unit", "tech": "Tech", "t": "Tech"}


# Event type grammar of the OCEL log (see reports/exploration.md, section 2).
GRAMMAR = [
    (re.compile(r"^(Command|Start|Complete) (Queue|Research|Build) ?(.*)$"), None),
    (re.compile(r"^Set Gather Point ?(.*)$"), "SET_GATHER_POINT"),
    (re.compile(r"^Gather ?(.*)$"), "GATHER"),
    (re.compile(r"^Create Initial ?(.*)$"), "CREATE_INITIAL"),
]


def parse_event_type(ocel_type):
    """Return (action, phase, object_name), or None if the type fits no grammar rule.

    The object name may be empty: "Start Research " exists in the log.
    """
    for pattern, fixed_action in GRAMMAR:
        match = pattern.match(ocel_type)
        if match:
            if fixed_action:
                return fixed_action, None, match.group(1)
            return match.group(2).upper(), match.group(1).upper(), match.group(3)
    return None


def normalize(name):
    """Case-insensitive, letters and digits only: "Double-Bit Axe" == "DoubleBit Axe"."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _require(path):
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run scripts/00_fetch_techtree.py first.")
    return path


def _cost(entry):
    return {k.lower(): v for k, v in (entry or {}).get("Cost", {}).items()}


def _new_node(category, numeric, name, entry, upgrade):
    return {
        "category": category, "id": numeric, "name": name, "names": {name},
        "internal_name": entry.get("internal_name") if entry else None,
        "node_types": set(), "ages": set(), "age_by_civ": {}, "producers": set(),
        "links": set(), "prereqs": set(), "civs_available": set(),
        "time": (entry.get("TrainTime") if category != "Tech" else entry.get("ResearchTime")) if entry else None,
        "cost": _cost(entry),
        "is_upgrade": upgrade is not None,
        "upgrade_time": upgrade.get("ResearchTime") if upgrade else None,
        "upgrade_cost": _cost(upgrade),
        "in_data_json": entry is not None,
    }


def load_2026():
    data = json.loads(_require(DATA_JSON_2026).read_text())
    strings = json.loads(_require(DIR_2026 / "strings_en.json").read_text())
    tables = {"Building": data["data"]["Building"], "Tech": data["data"]["Tech"], "Unit": data["data"]["Unit"]}
    upgrades = data["data"]["unit_upgrades"]
    nodes, civ_available = {}, {}
    for civ in data["civs"]:
        tree = json.loads(_require(DIR_2026 / f"trees/{civ.upper()}.json").read_text())
        available = {"Building": set(), "Tech": set(), "Unit": set()}
        for raw in tree["buildings"] + tree["units_techs"]:
            category, numeric, _ = raw["id"].split("_")
            numeric = int(numeric)
            key = (category, numeric)
            if key not in nodes:
                nodes[key] = _new_node(category, numeric, raw["name"], tables[category].get(str(numeric)),
                                       upgrades.get(str(numeric)) if category == "Unit" else None)
            node = nodes[key]
            node["names"].add(raw["name"])
            node["node_types"].add(raw["node_type"])
            node["ages"].add(raw["age_id"])
            node["producers"].add(raw["building_id"])
            node["links"].add((raw["link_id"], raw["link_node_type"]))
            if raw["link_id"] not in (None, -1) and raw["link_node_type"] in CATEGORY_OF_2026_LINK:
                node["prereqs"].add((CATEGORY_OF_2026_LINK[raw["link_node_type"]], raw["link_id"]))
            if raw["node_status"] != "NotAvailable":
                node["civs_available"].add(civ)
                node["age_by_civ"][civ] = raw["age_id"]
                available[category].add(numeric)
        civ_available[civ] = available
    return SimpleNamespace(version="2026", civs=sorted(data["civs"]), nodes=nodes,
                           civ_available=civ_available, strings=strings, data=data)


def parse_techtree_js(text):
    """Return constants, lanes and connections from the 2023 js/techtree.js.

    Line comments are removed first: the file contains commented-out links such as
    `// [b(STABLE), u(CAMEL_RIDER)],` that are not part of the tree.
    """
    text = re.sub(r"//[^\n]*", "", text)
    constants = {name: int(value) for name, value in re.findall(r"^const (\w+) = (\d+);", text, re.M)}
    lanes = {}
    for lane, call, constant in re.findall(
            r"(\w+)lane\.rows\.\w+\.push\((building|unit|uniqueunit|tech)\((\w+)\)\)", text):
        lanes.setdefault(lane, []).append((CATEGORY_OF_JS_CALL[call], call, constant))
    for lane, constant in re.findall(r"(\w+)lane\.rows\.\w+\.push\(new Caret\(TYPES\.\w+, (\w+),", text):
        lanes.setdefault(lane, []).append(("Unit", "placeholder", constant))
    body = text[text.index("function getConnections"):]
    body = body[:body.index("];")]
    connections = [(CATEGORY_OF_JS_CALL[a], x, CATEGORY_OF_JS_CALL[b], y)
                   for a, x, b, y in re.findall(r"\[([but])\((\w+)\), ([but])\((\w+)\)\]", body)]
    return constants, lanes, connections


def load_2023():
    data = json.loads(_require(DIR_2023 / "data.json").read_text())
    strings = json.loads(_require(DIR_2023 / "strings_en.json").read_text())
    constants, lanes, connections = parse_techtree_js(_require(DIR_2023 / "techtree.js").read_text())
    tables = {"Building": data["data"]["buildings"], "Tech": data["data"]["techs"], "Unit": data["data"]["units"]}
    upgrades = data["data"]["unit_upgrades"]

    nodes = {}

    def node(category, numeric):
        key = (category, numeric)
        if key not in nodes:
            entry = tables[category].get(str(numeric))
            upgrade = upgrades.get(str(numeric)) if category == "Unit" else None
            if entry:
                name = strings.get(str(entry["LanguageNameId"]), entry["internal_name"])
            else:
                name = (upgrade or {}).get("internal_name", f"{category} {numeric}")
            nodes[key] = _new_node(category, numeric, name, entry, upgrade)
        return nodes[key]

    for category, table in tables.items():
        for key in table:
            node(category, int(key))

    civ_available = {}
    unique_slots = {"castleAgeUniqueUnit": ("Unit", "UniqueUnit"), "imperialAgeUniqueUnit": ("Unit", "UniqueUnit"),
                    "castleAgeUniqueTech": ("Tech", "UniqueTech"), "imperialAgeUniqueTech": ("Tech", "UniqueTech")}
    unique_ids = {"Unit": set(), "Tech": set()}
    for civ, tree in data["techtrees"].items():
        available = {"Building": set(), "Tech": set(), "Unit": set()}
        items = ([("Building", e) for e in tree["buildings"]] + [("Tech", e) for e in tree["techs"]]
                 + [("Unit", e) for e in tree["units"]])
        for slot, (category, node_type) in unique_slots.items():
            items.append((category, {"id": tree["unique"][slot], "age": 3 if slot.startswith("castle") else 4}))
            unique_ids[category].add(tree["unique"][slot])
        for category, entry in items:
            n = node(category, entry["id"])
            n["civs_available"].add(civ)
            n["ages"].add(entry["age"])
            n["age_by_civ"][civ] = entry["age"]
            available[category].add(entry["id"])
        civ_available[civ] = available

    # Producers: every unit and tech in a lane is produced at the lane's first building.
    # The castle lane's placeholders stand for each civ's unique unit and techs.
    node_type_of_call = {"building": "Building", "unit": "Unit", "uniqueunit": "UniqueUnit",
                         "tech": "Research", "placeholder": "UniqueUnit"}
    for lane in lanes.values():
        producer = next((constants[c] for cat, _, c in lane if cat == "Building" and c in constants), None)
        for category, call, constant in lane:
            if call == "placeholder":
                targets, node_type = [nodes[("Unit", i)] for i in unique_ids["Unit"]], "UniqueUnit"
            elif constant.startswith("UNIQUE_TECH"):
                targets, node_type = [nodes[("Tech", i)] for i in unique_ids["Tech"]], "UniqueTech"
            elif constant in constants:
                targets, node_type = [node(category, constants[constant])], node_type_of_call[call]
            else:
                continue
            for n in targets:
                n["node_types"].add(node_type)
                if category != "Building" and producer is not None:
                    n["producers"].add(producer)
    for a_cat, a, b_cat, b in connections:
        if a in constants and b in constants:
            node(b_cat, constants[b])["prereqs"].add((a_cat, constants[a]))
            node(b_cat, constants[b])["links"].add((constants[a], a_cat))

    for n in nodes.values():
        if not n["node_types"]:
            n["node_types"].add({"Building": "Building", "Tech": "Research", "Unit": "Unit"}[n["category"]])
    civs = sorted(data["techtrees"])
    return SimpleNamespace(version="2023", civs=civs, nodes=nodes, civ_available=civ_available,
                           strings=strings, data=data, constants=constants, lanes=lanes,
                           connections=connections, unique_ids=unique_ids)
