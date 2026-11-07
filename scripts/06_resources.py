#!/usr/bin/env python3
"""Phase 5: starting resources, age over time, resource snapshots, plausibility check.

Fills setting, session_start, session_age and resource_snapshot (all estimated), then
runs aoe2_plausibility.is_plausible on every real player decision. Real actions should
almost always pass, so the violation rate is the checker's false-alarm rate.

Writes reports/resources.md.
Usage: python3 scripts/06_resources.py [--db PATH] [--deduction-phase COMMAND|START]
           [--start FOOD,WOOD,GOLD,STONE] [--max-gather-rate R] [--no-civ-start-adjustments]
"""
import argparse
import bisect
import collections
import csv
import math
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aoe2_plausibility import AGE_TECHS, AGE_UP_SPEED, BUILDING_REQUIREMENT_EXEMPT, Checker  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data/processed/aoe2.db"
START_RESOURCES = ROOT / "data/manual/start_resources.csv"
REPORT = ROOT / "reports/resources.md"
STANDARD_START = (200, 200, 100, 200)  # DE random map, standard resources
REASONS = ("civ", "age", "prerequisite", "producer", "spending")
DEFAULT_GATHER_RATE = 0.5   # calibrated: smallest rate with < 0.5 % spending alarms on real decisions
CALIBRATION_TARGET = 0.005  # in sessions without any flag (see reports/resources.md)


def required_rate(checker, session, t, obj):
    """Smallest gather rate (resources per villager-second) at which this action passes the spending check."""
    data = checker.session(session)
    i = bisect.bisect_left(data["ded_times"], t)
    over = (data["ded_cum"][i - 1] if i else 0) + sum(checker.obj[obj]["cost"]) - data["start_total"]
    if over <= 0:
        return 0.0
    seconds = checker.villager_seconds(data, t)
    return over / seconds if seconds > 0 else math.inf


def md_table(headers, rows):
    def cell(value):
        return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def pct(part, whole):
    return f"{100 * part / whole:.2f}%" if whole else "n/a"


def load_settings(con, args):
    settings = {
        "deduction_phase_queue_research": (args.deduction_phase, "phase whose events deduct unit and research costs; "
                                           "buildings always deduct at BUILD START (the log has no build command)"),
        "deduction_phase_build": ("START", "fixed"),
        "start_resources": (",".join(map(str, args.start)), "food,wood,gold,stone before civ adjustments"),
        "civ_start_adjustments": ("off" if args.no_civ_start_adjustments else "on",
                                  "data/manual/start_resources.csv, only for sessions narrowed to one civ"),
        "max_gather_rate": (str(args.max_gather_rate), "resources per villager-second, an upper bound for the "
                            "spending check, not an income estimate"),
        "age_rule": ("earliest(command, object)", "age = earliest of (first age COMMAND + research time) and (first use "
                     "of an object that needs the age)"),
    }
    con.executemany("INSERT INTO setting VALUES (?, ?, ?)", [(k, v, n) for k, (v, n) in settings.items()])
    return settings


def fill_session_start(con, args):
    adjustments = {}
    if not args.no_civ_start_adjustments:
        with open(START_RESOURCES, newline="") as handle:
            for row in csv.DictReader(handle):
                adjustments[row["civ"]] = (int(row["food"]), int(row["wood"]), int(row["gold"]), int(row["stone"]),
                                           row["evidence"])
    single = dict(con.execute("""SELECT s.id, MIN(m.civ_id) FROM session s JOIN civ_group_member m
                                 ON m.group_id = s.civ_group_id GROUP BY s.id HAVING COUNT(*) = 1"""))
    civ_name = dict(con.execute("SELECT id, name FROM civilization"))
    villagers = dict(con.execute("""SELECT session_id, COUNT(*) FROM event
                                    WHERE ocel_type = 'Create Initial Villager' GROUP BY 1"""))
    rows = []
    for (session,) in con.execute("SELECT id FROM session ORDER BY id").fetchall():
        food, wood, gold, stone = args.start
        civ, detail = None, "standard start"
        name = civ_name.get(single.get(session))
        if name in adjustments:
            df, dw, dg, ds, evidence = adjustments[name]
            food, wood, gold, stone = food + df, wood + dw, gold + dg, stone + ds
            civ, detail = single[session], f"standard start adjusted for {name}: {evidence}"
        rows.append((session, food, wood, gold, stone, villagers.get(session, 0), civ, detail))
    con.executemany("""INSERT INTO session_start (session_id, food, wood, gold, stone, villagers, civ_id, source_detail)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", rows)


def fill_session_age(con, checker):
    age_tech = checker.age_tech
    rows = []
    current, uses = None, []

    def flush(session, uses):
        data = checker.session(session)
        candidates = data["candidates"]
        speed = AGE_UP_SPEED.get(checker.civ_name[next(iter(candidates))], 1.0) if len(candidates) == 1 else 1.0
        command, first_object = {}, {}
        for t, o, phase in uses:
            if phase == "COMMAND" and o in checker.age_of_tech:
                command.setdefault(checker.age_of_tech[o], t)
            needed = checker.needed_age(o, candidates)
            if needed and needed > 1:
                for age in range(2, needed + 1):
                    first_object.setdefault(age, (t, o))
        reached = {1: (0, "start", None, None, None)}
        for age in (2, 3, 4):
            cmd = command.get(age)
            cmd_reached = cmd + checker.obj[age_tech[age]]["time"] / speed if cmd is not None else None
            obj_t, obj_id = first_object.get(age, (None, None))
            options = [(v, src) for v, src in ((cmd_reached, "command"), (obj_t, "object")) if v is not None]
            if options:
                value, source = min(options)
                reached[age] = (round(value), source, cmd, obj_t, obj_id)
        for age in (3, 2):  # a later age implies the earlier ones
            if age + 1 in reached and (age not in reached or reached[age + 1][0] < reached[age][0]):
                old = reached.get(age, (None, None, None, None, None))
                reached[age] = (reached[age + 1][0], "higher_age", old[2], old[3], old[4])
        return [(session, age, *values) for age, values in sorted(reached.items())]

    for session, t, o, phase in con.execute("""SELECT session_id, time_s, game_object_id, phase FROM event
                                               WHERE in_vocabulary = 1 ORDER BY session_id, id"""):
        if session != current:
            if current is not None:
                rows += flush(current, uses)
            current, uses = session, []
        uses.append((t, o, phase))
    if current is not None:
        rows += flush(current, uses)
    with_rows = {r[0] for r in rows}
    rows += [(s, 1, 0, "start", None, None, None) for (s,) in con.execute("SELECT id FROM session") if s not in with_rows]
    con.executemany("""INSERT INTO session_age (session_id, age, reached_s, source, command_s, object_s, object_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""", rows)


def fill_snapshots(con, phase):
    con.execute("DROP TABLE IF EXISTS temp.d")
    con.execute("""
        CREATE TEMP TABLE d AS
        SELECT e.id, e.session_id, e.time_s, e.in_vocabulary AS vocab,
               CASE WHEN ded THEN g.cost_food ELSE 0 END AS food, CASE WHEN ded THEN g.cost_wood ELSE 0 END AS wood,
               CASE WHEN ded THEN g.cost_gold ELSE 0 END AS gold, CASE WHEN ded THEN g.cost_stone ELSE 0 END AS stone,
               (e.ocel_type = 'Complete Queue Villager') AS vil
        FROM (SELECT *, ((action IN ('QUEUE', 'RESEARCH') AND phase = ?) OR (action = 'BUILD' AND phase = 'START')) AS ded
              FROM event) e
        LEFT JOIN game_object g ON g.id = e.game_object_id
        WHERE e.in_vocabulary = 1 OR e.ded OR e.ocel_type = 'Complete Queue Villager'""", (phase,))
    con.execute("""
        INSERT INTO resource_snapshot (event_id, session_id, time_s, spent_food, spent_wood, spent_gold, spent_stone,
                                       min_gathered, villagers, age, age_source)
        SELECT x.id, x.session_id, x.time_s, x.f, x.w, x.g, x.s,
               MAX(0, x.f + x.w + x.g + x.s - (st.food + st.wood + st.gold + st.stone)),
               st.villagers + x.v, a.age, a.source
        FROM (SELECT id, session_id, time_s, vocab,
                     SUM(food) OVER w AS f, SUM(wood) OVER w AS w, SUM(gold) OVER w AS g, SUM(stone) OVER w AS s,
                     SUM(vil) OVER w AS v
              FROM d WINDOW w AS (PARTITION BY session_id ORDER BY id ROWS UNBOUNDED PRECEDING)) x
        JOIN session_start st ON st.session_id = x.session_id
        JOIN session_age a ON a.session_id = x.session_id
             AND a.age = (SELECT MAX(age) FROM session_age b WHERE b.session_id = x.session_id AND b.reached_s <= x.time_s)
        WHERE x.vocab = 1""")


def validate(con, checker):
    counts = collections.Counter()
    by_object = collections.defaultdict(collections.Counter)
    example = {}
    by_action = collections.defaultdict(lambda: [0, 0])
    flagged_sessions = {r[0] for r in con.execute("""SELECT id FROM session WHERE short_match OR no_commands
                                                     OR outlier_commands OR has_unmapped_events""")}
    clean = [0, 0]
    total = 0
    for session, t, o, action in con.execute("""SELECT session_id, time_s, game_object_id, action FROM event
                                                WHERE in_vocabulary = 1 ORDER BY session_id, id""").fetchall():
        reasons = checker.check(session, t, o)
        total += 1
        by_action[action][0] += 1
        if session not in flagged_sessions:
            clean[0] += 1
        if reasons:
            counts["any"] += 1
            by_action[action][1] += 1
            if session not in flagged_sessions:
                clean[1] += 1
            for code in {code for code, _ in reasons}:
                counts[code] += 1
                by_object[checker.obj[o]["name"]][code] += 1
                example.setdefault((checker.obj[o]["name"], code), next(d for c, d in reasons if c == code))
            by_object[checker.obj[o]["name"]]["any"] += 1
    # Negative control: same session and time, a random vocabulary object instead of the real one.
    vocabulary = [r[0] for r in con.execute("SELECT DISTINCT game_object_id FROM event WHERE in_vocabulary = 1 ORDER BY 1")]
    rng = random.Random(0)
    control = collections.Counter()
    control_total = 0
    need_real, need_random = [], []
    for session, t, o in con.execute("""SELECT session_id, time_s, game_object_id FROM event
                                        WHERE in_vocabulary = 1 ORDER BY session_id, id""").fetchall():
        substitute = rng.choice(vocabulary)
        if session not in flagged_sessions:
            need_real.append(required_rate(checker, session, t, o))
            if substitute != o:
                need_random.append(required_rate(checker, session, t, substitute))
        if substitute == o:
            continue
        reasons = checker.check(session, t, substitute)
        control_total += 1
        control["any"] += bool(reasons)
        for code in {code for code, _ in reasons}:
            control[code] += 1
    return {"total": total, "counts": counts, "by_object": by_object, "example": example, "by_action": by_action,
            "clean": clean, "control": control, "control_total": control_total,
            "need_real": sorted(need_real), "need_random": need_random}


def write_report(con, settings, result, runtime):
    q = con.execute
    out = ["# Resources, age and plausibility (Phase 5)",
           f"Generated by `scripts/06_resources.py` in {runtime:.0f} s. All values in `session_start`, `session_age` "
           "and `resource_snapshot` are estimates (`estimated = 1`)."]
    out.append("## Settings")
    out.append(md_table(["Setting", "Value", "Note"], [(k, v, n) for k, (v, n) in settings.items()]))
    out.append("Civ rules used by the checker (2023 help texts): building requirements waived for "
               + ", ".join(f"{c} ({e})" for c, e in BUILDING_REQUIREMENT_EXEMPT.items())
               + "; faster age-ups for " + ", ".join(f"{c} (×{f})" for c, f in AGE_UP_SPEED.items())
               + ", applied only when the session is narrowed to that civ.")

    out.append("## Row counts")
    out.append(md_table(["Table", "Rows"], [(t, f"{q(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:,}")
                                           for t in ("setting", "session_start", "session_age", "resource_snapshot")]))
    adjusted = q("""SELECT v.name, COUNT(*) FROM session_start s JOIN civilization v ON v.id = s.civ_id
                    GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    out.append("Starting resources adjusted for identified civs: " +
               (", ".join(f"{c} ({n})" for c, n in adjusted) or "none") +
               f". All other sessions use the standard start {settings['start_resources'][0]}.")

    out.append("## Age over time")
    rows = []
    for age in (2, 3, 4):
        reached = q("SELECT COUNT(*) FROM session_age WHERE age=?", (age,)).fetchone()[0]
        sources = dict(q("SELECT source, COUNT(*) FROM session_age WHERE age=? GROUP BY 1", (age,)))
        no_cmd = q("SELECT COUNT(*) FROM session_age WHERE age=? AND command_s IS NULL", (age,)).fetchone()[0]
        times = [r[0] for r in q("SELECT reached_s FROM session_age WHERE age=?", (age,))]
        rows.append((f"{age} ({AGE_TECHS[age]})", reached, sources.get("command", 0), sources.get("object", 0),
                     sources.get("higher_age", 0), no_cmd,
                     f"{statistics.median(times) / 60:.1f} min" if times else "-"))
    out.append(md_table(["Age", "Sessions reaching it", "Source: command", "Source: object", "Source: higher age",
                         "Without any age command", "Median time reached"], rows))
    out.append("*Source* says which evidence came first. 'Without any age command' counts sessions whose log has no "
               "command for that age although later evidence shows it was reached: the log misses these commands.")

    out.append("## Snapshots")
    rows = []
    for minute in (5, 10, 15, 20, 30):
        values = q("""SELECT r.villagers, r.min_gathered, r.spent_food + r.spent_wood + r.spent_gold + r.spent_stone
                      FROM resource_snapshot r JOIN (SELECT session_id, MAX(event_id) AS e FROM resource_snapshot
                      WHERE time_s <= ? GROUP BY session_id) last ON last.e = r.event_id""", (minute * 60,)).fetchall()
        if values:
            rows.append((minute, len(values), statistics.median(v[0] for v in values),
                         statistics.median(v[2] for v in values), statistics.median(v[1] for v in values)))
    out.append(md_table(["Minute", "Sessions", "Median villagers", "Median total spent", "Median min. gathered"], rows))
    out.append("Villagers are starting villagers plus villagers completed so far (deaths are not in the log, so this "
               "is an upper bound on villagers alive). Spending counts every logged command; cancelled production "
               "is not visible, so spending can be overestimated.")

    out.append("## Plausibility check on real decisions (false-alarm rate)")
    total, counts = result["total"], result["counts"]
    out.append(f"`is_plausible` was run on all {total:,} in-vocabulary decisions. **{pct(counts['any'], total)}** are "
               f"flagged ({counts['any']:,}); in sessions without any session flag: "
               f"{pct(result['clean'][1], result['clean'][0])}.")
    out.append(md_table(["Reason", "Decisions flagged", "Rate"], [(r, f"{counts[r]:,}", pct(counts[r], total))
                                                                  for r in REASONS]))
    out.append("By action:\n\n" + md_table(["Action", "Decisions", "Flagged", "Rate"], [
        (a, f"{n:,}", f"{k:,}", pct(k, n)) for a, (n, k) in sorted(result["by_action"].items())]))
    control, control_total = result["control"], result["control_total"]
    out.append("**Negative control** (does the checker catch anything?): every decision was replaced by a random object "
               "from the 265-object vocabulary, at the same session and time (seed 0). "
               f"{pct(control['any'], control_total)} of these {control_total:,} substitutes are flagged.\n\n" +
               md_table(["Reason", "Real decisions", "Random substitutes"],
                        [(r, pct(counts[r], total), pct(control[r], control_total)) for r in REASONS]))
    need_real, need_random = result["need_real"], result["need_random"]
    smallest = need_real[math.ceil((1 - CALIBRATION_TARGET) * len(need_real)) - 1]
    rates = sorted({0.3, 0.4, 0.5, 0.6, 0.8, 1.0, float(settings["max_gather_rate"][0])})
    out.append("**Gather-rate calibration** (sessions without flags). For each decision, the smallest gather rate at "
               "which it passes the spending check was computed. The smallest rate that keeps real decisions under "
               f"{CALIBRATION_TARGET:.1%} spending alarms is **{smallest:.3f}** resources per villager-second; the default "
               f"is {DEFAULT_GATHER_RATE}. Even there, few random substitutes fail on spending, so the spending check is "
               "a loose sanity bound only; civ, age, producer and prerequisite do the real work.\n\n" +
               md_table(["Gather rate", "Real decisions with a spending alarm", "Random substitutes with a spending alarm"],
                        [(r, pct(sum(x > r for x in need_real), len(need_real)),
                          pct(sum(x > r for x in need_random), len(need_random))) for r in rates]))
    top = sorted(result["by_object"].items(), key=lambda kv: (-kv[1]["any"], kv[0]))[:10]
    out.append("Top 10 violating objects:\n\n" + md_table(
        ["Object", "Flagged decisions"] + list(REASONS) + ["Example"],
        [(name, f"{c['any']:,}", *[c[r] or "" for r in REASONS],
          result["example"].get((name, max(REASONS, key=lambda r: c[r])), ""))
         for name, c in top]))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n\n".join(out) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Estimate resources and age, validate the plausibility check.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--deduction-phase", choices=("COMMAND", "START"), default="COMMAND")
    parser.add_argument("--start", type=lambda s: tuple(int(x) for x in s.split(",")), default=STANDARD_START)
    parser.add_argument("--max-gather-rate", type=float, default=DEFAULT_GATHER_RATE)
    parser.add_argument("--no-civ-start-adjustments", action="store_true")
    args = parser.parse_args()
    started = time.time()
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON")
    if con.execute("SELECT COUNT(*) FROM session WHERE civ_group_id IS NOT NULL").fetchone()[0] == 0:
        sys.exit("No civ groups. Run scripts/05_civ_inference.py first.")
    with con:
        for table in ("resource_snapshot", "session_age", "session_start", "setting"):
            con.execute(f"DELETE FROM {table}")
        settings = load_settings(con, args)
        fill_session_start(con, args)
        checker = Checker(con, {"deduction_phase_queue_research": args.deduction_phase,
                                "max_gather_rate": args.max_gather_rate})
        fill_session_age(con, checker)
        print(f"{time.time() - started:5.0f}s  session_start, session_age", flush=True)
        fill_snapshots(con, args.deduction_phase)
        print(f"{time.time() - started:5.0f}s  resource_snapshot", flush=True)
    if con.execute("PRAGMA foreign_key_check").fetchall():
        sys.exit("Foreign key violations")
    result = validate(con, Checker(con, {"deduction_phase_queue_research": args.deduction_phase,
                                         "max_gather_rate": args.max_gather_rate}))
    print(f"{time.time() - started:5.0f}s  validation", flush=True)
    write_report(con, settings, result, time.time() - started)
    con.close()
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
