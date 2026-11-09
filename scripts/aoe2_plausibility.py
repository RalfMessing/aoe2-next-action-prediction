"""Plausibility of an action for a session at a given time, against the 2023 rules.

    from aoe2_plausibility import is_plausible
    ok, reasons = is_plausible(session_id, time_s, game_object_id)

`reasons` lists (code, detail) for every failed check:
  civ          no candidate civ of the session has the object
  age          the session cannot be in the required age yet
  prerequisite a required building or tech (incl. "n of" groups) is not present yet
  producer     no building or unit that produces the object is present yet
  spending     total spending would exceed the starting resources plus an upper bound on
               what the session's villagers could have gathered so far

Evidence counts only if it happened before `time_s` (age from objects: strictly before),
so checking a real decision does not prove itself. Everything is an estimate:
- presence of a building = its BUILD START (construction speed depends on builders);
- presence of a tech = first COMMAND + research time (the earliest it can be finished);
- age = max(first age COMMAND + research time, first earlier use of an object needing it);
- the gather bound uses MAX_GATHER_RATE resources per villager-second, a generous upper
  bound and not an estimate of real income (unit deaths, trade, relics and tributes are
  unknown);
- civ cost bonuses are not modeled;
- the first Town Center of a session without a starting TC is exempt from the age check.
Requires the tables filled by 03, 04, 05 and 06 (session_start).
"""
import bisect
import collections
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data/processed/aoe2.db"
EQUIVALENTS = ROOT / "data/manual/replacement_equivalents.csv"

DEFAULT_SETTINGS = {
    "deduction_phase_queue_research": "COMMAND",  # or START; buildings always at START
    "max_gather_rate": 0.5,                       # resources per villager-second (calibrated upper bound)
}
AGE_TECHS = {2: "Feudal Age", 3: "Castle Age", 4: "Imperial Age"}
# 2023 civ help texts.
BUILDING_REQUIREMENT_EXEMPT = {"Khmer": "'No buildings required to advance to the next age or to unlock other buildings'"}
AGE_UP_SPEED = {"Malay": 1.66}  # 'Advancing to the next age 66% faster'
VILLAGER_TREE_ID = 83


class Checker:
    def __init__(self, con, settings=None):
        self.con = con
        if settings is None:  # use what 06_resources.py ran with, if recorded
            try:
                stored = dict(con.execute("SELECT key, value FROM setting"))
            except sqlite3.OperationalError:
                stored = {}
            settings = {k: (float(stored[k]) if k == "max_gather_rate" else stored[k])
                        for k in DEFAULT_SETTINGS if k in stored}
        self.settings = dict(DEFAULT_SETTINGS, **settings)
        q = con.execute
        self.obj = {i: {"type": t, "name": n, "time": tm or 0, "cost": (f, w, g, s)}
                    for i, t, n, tm, f, w, g, s in q("""SELECT id, type, name, time, cost_food, cost_wood, cost_gold,
                                                        cost_stone FROM game_object""")}
        self.ids_by_name = collections.defaultdict(set)
        for i, o in self.obj.items():
            self.ids_by_name[(o["type"], o["name"])].add(i)
        self.civ_name = dict(q("SELECT id, name FROM civilization"))
        self.all_civs = frozenset(self.civ_name)
        self.civ_obj = collections.defaultdict(dict)  # obj -> civ -> (age, producer)
        for civ, o, age, producer in q("SELECT civ_id, object_id, required_age, produced_at FROM civilization_object"):
            self.civ_obj[o][civ] = (age, producer)
        # A logged base name also stands for the civ's replacement building (Mill = Folwark).
        self.alias = collections.defaultdict(set)
        with open(EQUIVALENTS, newline="") as handle:
            for row in csv.DictReader(handle):
                base = min(self.ids_by_name[(row["base_type"], row["base_name"])])
                replacement = min(self.ids_by_name[(row["replacement_type"], row["replacement_name"])])
                for civ, value in self.civ_obj[replacement].items():
                    self.civ_obj[base].setdefault(civ, value)
                self.alias[replacement].add(base)
        self.deps = collections.defaultdict(list)
        for o, req, group, count, weight in q("SELECT object_id, requires_id, req_group, req_count, weight FROM dependency"):
            self.deps[o].append((req, group, count, weight))
        self.age_tech = {age: min(self.ids_by_name[("TECHNOLOGY", name)]) for age, name in AGE_TECHS.items()}
        self.age_of_tech = {v: k for k, v in self.age_tech.items()}
        self.villager = q("SELECT id FROM game_object WHERE type='UNIT' AND tree_id=?", (VILLAGER_TREE_ID,)).fetchone()[0]
        self.exempt = {self.civ_name_to_id(c) for c in BUILDING_REQUIREMENT_EXEMPT if self.civ_name_to_id(c)}
        self.session_cache = {}

    def civ_name_to_id(self, name):
        return next((i for i, n in self.civ_name.items() if n == name), None)

    # -- per-session data -------------------------------------------------------------------------
    def needed_age(self, obj, candidates):
        ages = [a for c, (a, _) in self.civ_obj.get(obj, {}).items() if c in candidates]
        return min(ages) if ages else None

    def session(self, session_id):
        if session_id in self.session_cache:
            return self.session_cache[session_id]
        q = self.con.execute
        members = {r[0] for r in q("""SELECT m.civ_id FROM session s JOIN civ_group_member m
                                      ON m.group_id = s.civ_group_id WHERE s.id = ?""", (session_id,))}
        candidates = frozenset(members) or self.all_civs
        speed = 1.0
        if len(candidates) == 1:
            speed = AGE_UP_SPEED.get(self.civ_name[next(iter(candidates))], 1.0)
        start = q("SELECT food + wood + gold + stone, villagers FROM session_start WHERE session_id=?",
                  (session_id,)).fetchone() or (700, 3)
        phase = self.settings["deduction_phase_queue_research"]
        present = {}           # building name / object id -> earliest time present
        age_command = {}       # age -> first command time
        object_use = []        # (time, needed age) of in-vocabulary uses
        deductions = []        # (time, total cost)
        villager_done = []     # completion times of villagers
        for t, action, ph, o, ocel_type, vocab in q("""
                SELECT time_s, action, phase, game_object_id, ocel_type, in_vocabulary FROM event
                WHERE session_id = ? AND (in_vocabulary = 1 OR action = 'CREATE_INITIAL'
                      OR (action IN ('QUEUE', 'RESEARCH') AND phase = ?) OR (action = 'BUILD' AND phase = 'START')
                      OR ocel_type = 'Complete Queue Villager')
                ORDER BY id""", (session_id, phase)):
            if o is not None and o in self.obj:
                info = self.obj[o]
                if action == "CREATE_INITIAL":
                    self.mark(present, o, 0)
                elif action == "BUILD" and ph == "START":
                    self.mark(present, o, t)
                elif ph == "COMMAND":
                    self.mark(present, o, t + info["time"])
                    if o in self.age_of_tech:
                        age_command.setdefault(self.age_of_tech[o], t)
                if (action in ("QUEUE", "RESEARCH") and ph == phase) or (action == "BUILD" and ph == "START"):
                    deductions.append((t, sum(info["cost"])))
                if vocab:
                    needed = self.needed_age(o, candidates)
                    if needed and needed > 1:
                        object_use.append((t, needed))
            if ocel_type == "Complete Queue Villager":
                villager_done.append(t)
        deductions.sort()
        cumulative, total = [], 0
        for _, cost in deductions:
            total += cost
            cumulative.append(total)
        villager_done.sort()
        # villager-seconds up to each completion time, for the gather bound
        vs_at, n, last, acc = [], start[1], 0, 0.0
        for t in villager_done:
            acc += n * (t - last)
            vs_at.append(acc)
            n, last = n + 1, t
        age_cmd_reached = {}
        for age, t in age_command.items():
            age_cmd_reached[age] = t + self.obj[self.age_tech[age]]["time"] / speed
        first_use = {}
        for t, needed in sorted(object_use):
            for age in range(2, needed + 1):
                first_use.setdefault(age, t)
        data = {"candidates": candidates, "start_total": start[0], "start_villagers": start[1], "present": present,
                "age_cmd": age_cmd_reached, "first_use": first_use, "ded_times": [d[0] for d in deductions],
                "ded_cum": cumulative, "vil_times": villager_done, "vil_secs": vs_at}
        self.session_cache = {session_id: data}  # keep one session; validation runs session by session
        return data

    def mark(self, present, o, t):
        keys = [o] + ([("name", self.obj[o]["name"])] if self.obj[o]["type"] == "BUILDING" else [])
        for key in keys:
            if key not in present or t < present[key]:
                present[key] = t

    def is_present(self, data, o, t):
        """Built/researched/trained by time t (a building counts by name, aliases included)."""
        if o == self.villager:
            return True
        candidates = [o] + list(self.alias.get(o, ()))
        for c in candidates:
            keys = [c] + ([("name", self.obj[c]["name"])] if self.obj[c]["type"] == "BUILDING" else [])
            if any(k in data["present"] and data["present"][k] <= t for k in keys):
                return True
        return False

    def current_age(self, data, t):
        age = 1
        for a in (2, 3, 4):
            if data["age_cmd"].get(a, float("inf")) <= t or data["first_use"].get(a, float("inf")) < t:
                age = a
        return age

    def villager_seconds(self, data, t):
        i = bisect.bisect_right(data["vil_times"], t)
        base = data["vil_secs"][i - 1] if i else 0.0
        last = data["vil_times"][i - 1] if i else 0
        return base + (data["start_villagers"] + i) * (t - last)

    # -- the check ---------------------------------------------------------------------------------
    def check(self, session_id, time_s, obj):
        data = self.session(session_id)
        candidates = data["candidates"]
        reasons = []
        per_civ = {c: v for c, v in self.civ_obj.get(obj, {}).items() if c in candidates}
        if not per_civ:
            reasons.append(("civ", "no candidate civ has it"))
        else:
            needed = min(a for a, _ in per_civ.values())
            age = self.current_age(data, time_s)
            # Maps without a starting Town Center: the first TC is built in the Dark Age.
            first_tc = (self.obj[obj]["type"] == "BUILDING" and self.obj[obj]["name"] == "Town Center"
                        and not self.is_present(data, obj, time_s - 1))
            if needed > age and not first_tc:
                reasons.append(("age", f"needs age {needed}, estimated age {age}"))
            producers = {p for _, p in per_civ.values() if p is not None}
            if producers and not any(self.is_present(data, p, time_s) for p in producers):
                reasons.append(("producer", "no " + " / ".join(sorted(self.obj[p]["name"] for p in producers)) + " yet"))
        exempt = bool(candidates & self.exempt)
        groups = collections.defaultdict(lambda: [0, 0, []])
        for req, group, count, weight in self.deps.get(obj, []):
            if exempt and self.obj[req]["type"] == "BUILDING":
                continue
            met = self.is_present(data, req, time_s)
            if group is None:
                if not met:
                    reasons.append(("prerequisite", f"no {self.obj[req]['name']} yet"))
            else:
                groups[group][0] += weight if met else 0
                groups[group][1] = count
                groups[group][2].append(self.obj[req]["name"])
        for group, (got, count, names) in groups.items():
            if got < count:
                reasons.append(("prerequisite", f"{group}: {got} of {count} needed ({', '.join(names)})"))
        i = bisect.bisect_left(data["ded_times"], time_s)
        spent = (data["ded_cum"][i - 1] if i else 0) + sum(self.obj[obj]["cost"])
        bound = data["start_total"] + self.settings["max_gather_rate"] * self.villager_seconds(data, time_s)
        if spent > bound:
            reasons.append(("spending", f"spent {spent:.0f} > bound {bound:.0f}"))
        return reasons


_default_checker = None


def is_plausible(session_id, time_s, game_object_id, con=None, settings=None):
    """Return (True, []) or (False, [(code, detail), ...]) for doing game_object_id at time_s."""
    global _default_checker
    if con is not None:
        checker = Checker(con, settings)
    else:
        if _default_checker is None:
            _default_checker = Checker(sqlite3.connect(DEFAULT_DB), settings)
        checker = _default_checker
    reasons = checker.check(session_id, time_s, game_object_id)
    return not reasons, reasons
