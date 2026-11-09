#!/usr/bin/env python3
"""Phase 3: load matches, players, sessions, events and raw E2O links into aoe2.db.

Needs the static tables from 03_load_static.py. Replay tables are emptied and refilled,
so the script can be re-run on its own. The OCEL log is attached read-only, and the bulk
work is INSERT ... SELECT over temporary tables.

Writes reports/replay_load.md (source vs. target counts, mapping share, vocabulary,
flags, a sample session timeline).

Usage: python3 scripts/04_load_replays.py [--ocel PATH] [--db PATH]
"""
import argparse
import collections
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aoe2_rules import parse_event_type  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data/processed/aoe2.db"
DEFAULT_OCEL = ROOT / "data/raw/age_of_empires_ocel2.sqlite"
REPORT = ROOT / "reports/replay_load.md"

SHORT_MATCH_SECONDS = 300
OUTLIER_PERCENTILE = 99
RESOURCES = {"Food": "FOOD", "Wood": "WOOD", "Gold": "GOLD", "Stone": "STONE", "Relic": "RELIC", "None": "NONE"}
SAMPLE_MINUTES = 15
AGES = ("Feudal Age", "Castle Age", "Imperial Age")

# Main OCEL object of an event (event.object_ref). Lower rank wins; ties: lowest id.
# BUILD / CREATE_INITIAL: the object of the event's own type (the building or unit created).
# GATHER: the villager. QUEUE / RESEARCH / SET_GATHER_POINT: the producing building
# (any object that is not a villager, including untyped UO ids). Match, Session and Player
# are never the main object.
OBJECT_RANK_SQL = """
    CASE
        WHEN l.otype IN ('Match', 'Session', 'Player') THEN 9
        WHEN t.action IN ('BUILD', 'CREATE_INITIAL') AND l.otype = t.name THEN 0
        WHEN t.action = 'GATHER' AND l.otype = 'Villager' THEN 0
        WHEN t.action IN ('QUEUE', 'RESEARCH', 'SET_GATHER_POINT')
             AND (l.otype IS NULL OR l.otype <> 'Villager') THEN 1
        ELSE 2
    END"""


def md_table(headers, rows):
    def cell(value):
        return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def fold(summary, body):
    return f"<details><summary>{summary}</summary>\n\n{body}\n\n</details>"


def step(message, started):
    print(f"{time.time() - started:6.0f}s  {message}", flush=True)


def load(con, started):
    q = con.execute
    if q("SELECT COUNT(*) FROM game_object").fetchone()[0] == 0:
        sys.exit("game_object is empty. Run scripts/03_load_static.py first.")
    unknown = q("SELECT id FROM game_object WHERE type='UNKNOWN'").fetchone()[0]

    for table in ("event_object_link", "event", "session", "player", "match"):
        q(f"DELETE FROM {table}")
    step("emptied replay tables", started)

    # Event types: action, phase, mapped object, resource, vocabulary flag
    mapping = {(a, n): g for a, n, g in q("SELECT action, ocel_name, game_object_id FROM name_mapping")}
    q("""CREATE TEMP TABLE etype (ocel_type TEXT PRIMARY KEY, type_map TEXT, action TEXT, phase TEXT, name TEXT,
         game_object_id INTEGER, resource TEXT, in_vocab INTEGER)""")
    rows = []
    for ocel_type, type_map in q("SELECT ocel_type, ocel_type_map FROM src.event_map_type ORDER BY 1").fetchall():
        parsed = parse_event_type(ocel_type)
        if parsed is None:
            sys.exit(f"Event type outside the known grammar: {ocel_type!r}")
        action, phase, name = parsed
        resource = None
        if action in ("GATHER", "SET_GATHER_POINT"):
            if name not in RESOURCES:
                sys.exit(f"Unknown resource in {ocel_type!r}")
            resource = RESOURCES[name]
        game_object = mapping.get((action, name)) if action in ("QUEUE", "RESEARCH", "BUILD", "CREATE_INITIAL") else None
        if action in ("QUEUE", "RESEARCH", "BUILD") and (action, name) not in mapping:
            sys.exit(f"No name_mapping row for {action} {name!r}; re-run 03_load_static.py")
        decision = (action in ("QUEUE", "RESEARCH") and phase == "COMMAND") or (action == "BUILD" and phase == "START")
        in_vocab = int(decision and game_object is not None and game_object != unknown)
        rows.append((ocel_type, type_map, action, phase, name, game_object, resource, in_vocab))
    con.executemany("INSERT INTO etype VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

    # Events with time and sequence number
    q("CREATE TEMP TABLE ev0 (source_id TEXT PRIMARY KEY, ocel_type TEXT, ts INTEGER, seq INTEGER) WITHOUT ROWID")
    for ocel_type, type_map, *_ in rows:
        q(f"""INSERT INTO ev0 SELECT ocel_id, ?, unixepoch(ocel_time),
              CAST(substr(ocel_id, length(rtrim(ocel_id, '0123456789')) + 1) AS INTEGER)
              FROM src."event_{type_map}" """, (ocel_type,))
    if q("SELECT COUNT(*) FROM ev0 WHERE ts IS NULL").fetchone()[0]:
        sys.exit("Events with unparseable timestamps")
    step("read events from the per-type tables", started)

    # Raw links with the linked object's type (NULL for UO ids missing from the object table)
    q("CREATE TEMP TABLE ob (id TEXT PRIMARY KEY, type TEXT) WITHOUT ROWID")
    q("INSERT INTO ob SELECT ocel_id, ocel_type FROM src.object")
    q("""CREATE TEMP TABLE links AS SELECT l.ocel_event_id AS event, l.ocel_object_id AS oid, ob.type AS otype
         FROM src.event_object l LEFT JOIN ob ON ob.id = l.ocel_object_id""")
    q("CREATE INDEX temp.links_event ON links (event)")
    q("""CREATE TEMP TABLE evlink AS SELECT event,
           MAX(CASE WHEN otype = 'Session' THEN oid END) AS session_ref, SUM(otype = 'Session') AS n_sessions,
           MAX(CASE WHEN otype = 'Player' THEN oid END) AS player_ref, SUM(otype = 'Player') AS n_players
         FROM links GROUP BY event""")
    q("CREATE INDEX temp.evlink_event ON evlink (event)")
    bad = q("SELECT COUNT(*) FROM ev0 LEFT JOIN evlink k ON k.event = ev0.source_id "
            "WHERE k.n_sessions IS NOT 1 OR k.n_players > 1").fetchone()[0]
    if bad:
        sys.exit(f"{bad} events do not link to exactly one session (or link to several players)")
    q(f"""CREATE TEMP TABLE objref AS SELECT event, oid FROM (
            SELECT l.event, l.oid, ROW_NUMBER() OVER (PARTITION BY l.event ORDER BY rank, l.oid) AS rn
            FROM (SELECT l.event, l.oid, {OBJECT_RANK_SQL} AS rank FROM links l
                  JOIN ev0 e ON e.source_id = l.event JOIN etype t ON t.ocel_type = e.ocel_type) l
            WHERE rank < 9) WHERE rn = 1""")
    q("CREATE INDEX temp.objref_event ON objref (event)")
    step("resolved sessions, players and main objects from the E2O links", started)

    # Matches, players, sessions
    sessions = [r[0] for r in q("SELECT id FROM ob WHERE type='Session' ORDER BY id")]
    matches = {r[0] for r in q("SELECT id FROM ob WHERE type='Match'")}
    players = {r[0] for r in q("SELECT id FROM ob WHERE type='Player'")}
    parsed_sessions = []
    for session in sessions:
        match_part, player_part = session[1:].split("_", 1)
        match_ref, player_ref = "M" + match_part, "P" + player_part
        if match_ref not in matches or player_ref not in players:
            sys.exit(f"Session {session} refers to a match or player missing from the object table")
        parsed_sessions.append((session, match_ref, player_ref))
    q("CREATE TEMP TABLE smap (session_ref TEXT PRIMARY KEY, match_ref TEXT, player_ref TEXT) WITHOUT ROWID")
    con.executemany("INSERT INTO smap VALUES (?, ?, ?)", parsed_sessions)
    q("""CREATE TEMP TABLE mtime AS SELECT m.match_ref, MIN(e.ts) AS start_ts, MAX(e.ts) AS end_ts
         FROM ev0 e JOIN evlink k ON k.event = e.source_id JOIN smap m ON m.session_ref = k.session_ref
         GROUP BY m.match_ref""")
    q("""INSERT INTO match (source_id, start_time, duration)
         SELECT match_ref, datetime(start_ts, 'unixepoch'), end_ts - start_ts FROM mtime ORDER BY match_ref""")
    if q("SELECT COUNT(*) FROM match").fetchone()[0] != len(matches):
        sys.exit("Some matches have no events")
    q("INSERT INTO player (source_id) SELECT id FROM ob WHERE type='Player' ORDER BY id")
    q("""INSERT INTO session (source_id, match_id, player_id)
         SELECT m.session_ref, mt.id, p.id FROM smap m JOIN match mt ON mt.source_id = m.match_ref
         JOIN player p ON p.source_id = m.player_ref ORDER BY m.session_ref""")
    step("inserted matches, players, sessions", started)

    # Events, ordered by session and time so that ids follow the timeline
    q("""INSERT INTO event (source_id, session_id, player_id, action, phase, game_object_id, resource, time_s, seq,
                            in_vocabulary, object_ref, ocel_type)
         SELECT e.source_id, s.id, p.id, t.action, t.phase, t.game_object_id, t.resource,
                e.ts - mt.start_ts, e.seq, t.in_vocab, r.oid, e.ocel_type
         FROM ev0 e
         JOIN etype t ON t.ocel_type = e.ocel_type
         JOIN evlink k ON k.event = e.source_id
         JOIN session s ON s.source_id = k.session_ref
         JOIN smap m ON m.session_ref = k.session_ref
         JOIN mtime mt ON mt.match_ref = m.match_ref
         LEFT JOIN player p ON p.source_id = k.player_ref
         LEFT JOIN objref r ON r.event = e.source_id
         ORDER BY s.id, e.ts, e.seq""")
    step("inserted events", started)
    q("""INSERT INTO event_object_link (event_id, object_ref)
         SELECT e.id, l.oid FROM links l JOIN event e ON e.source_id = l.event ORDER BY e.id, l.oid""")
    step("inserted raw E2O links", started)

    # Flags
    q("UPDATE session SET short_match = 1 WHERE match_id IN (SELECT id FROM match WHERE duration < ?)",
      (SHORT_MATCH_SECONDS,))
    q("""UPDATE session SET no_commands = 1 WHERE id NOT IN
         (SELECT DISTINCT session_id FROM event WHERE phase = 'COMMAND')""")
    counts = sorted(r[0] for r in q("SELECT COUNT(*) FROM event WHERE phase='COMMAND' GROUP BY session_id"))
    threshold = counts[math.ceil(OUTLIER_PERCENTILE / 100 * len(counts)) - 1]  # nearest-rank percentile
    q("""UPDATE session SET outlier_commands = 1 WHERE id IN
         (SELECT session_id FROM event WHERE phase='COMMAND' GROUP BY session_id HAVING COUNT(*) > ?)""", (threshold,))
    q("""UPDATE session SET has_unmapped_events = 1 WHERE id IN
         (SELECT DISTINCT session_id FROM event WHERE action IN ('QUEUE', 'RESEARCH', 'BUILD')
          AND (game_object_id IS NULL OR game_object_id = ?))""", (unknown,))
    problems = q("PRAGMA foreign_key_check").fetchall()
    if problems:
        sys.exit(f"Foreign key violations: {problems[:5]}")
    q("ANALYZE main")
    step("set flags, checked foreign keys", started)
    return {"threshold": threshold, "command_counts": counts, "unknown": unknown}


def mmss(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def write_report(con, info, runtime):
    q = con.execute
    out = ["# Replay load (Phase 3)",
           f"Generated by `scripts/04_load_replays.py` in {runtime:.0f} s."]

    out.append("## Row counts")
    tables = ["match", "player", "session", "event", "event_object_link"]
    out.append(md_table(["Table", "Rows"], [(t, f"{q(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:,}") for t in tables]))

    out.append("## Source vs. target")
    source = dict(q("SELECT ocel_type, COUNT(*) FROM src.event GROUP BY 1"))
    target = dict(q("SELECT ocel_type, COUNT(*) FROM event GROUP BY 1"))
    per_type = {t: q(f'SELECT COUNT(*) FROM src."event_{m}"').fetchone()[0]
                for t, m in q("SELECT ocel_type, ocel_type_map FROM src.event_map_type")}
    mismatches = [(t, source.get(t, 0), per_type.get(t, 0), target.get(t, 0)) for t in sorted(set(source) | set(target))
                  if not source.get(t, 0) == per_type.get(t, 0) == target.get(t, 0)]
    links_source = q("SELECT COUNT(*) FROM src.event_object").fetchone()[0]
    links_target = q("SELECT COUNT(*) FROM event_object_link").fetchone()[0]
    sessions_source = q("SELECT COUNT(*) FROM src.object WHERE ocel_type='Session'").fetchone()[0]
    out.append(md_table(["Item", "Source", "Target", "Equal"], [
        ("events (`event` table)", f"{sum(source.values()):,}", f"{sum(target.values()):,}",
         "yes" if sum(source.values()) == sum(target.values()) else "**no**"),
        ("events (sum of `event_<Type>` tables)", f"{sum(per_type.values()):,}", f"{sum(target.values()):,}",
         "yes" if sum(per_type.values()) == sum(target.values()) else "**no**"),
        ("event types", len(source), len(target), "yes" if len(source) == len(target) else "**no**"),
        ("E2O links", f"{links_source:,}", f"{links_target:,}", "yes" if links_source == links_target else "**no**"),
        ("sessions", sessions_source, q("SELECT COUNT(*) FROM session").fetchone()[0], ""),
    ]))
    out.append(f"Event types whose count differs between `event`, `event_<Type>` and the target: {len(mismatches)}.")
    if mismatches:
        out.append(md_table(["ocel_type", "event", "event_<Type>", "target"], mismatches))
    out.append(fold(f"Counts for all {len(source)} event types", md_table(
        ["ocel_type", "Source", "Target"], [(t, f"{source[t]:,}", f"{target.get(t, 0):,}") for t in sorted(source)])))

    out.append("## Time")
    deltas = collections.Counter(r[0] for r in q("""
        WITH s AS (SELECT session_id, time_s, ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY time_s, seq) n
                   FROM event WHERE ocel_type = 'Start Queue Villager'),
             c AS (SELECT session_id, time_s, ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY time_s, seq) n
                   FROM event WHERE ocel_type = 'Complete Queue Villager')
        SELECT c.time_s - s.time_s FROM s JOIN c USING (session_id, n)"""))
    villager_time = q("SELECT time FROM game_object WHERE type='UNIT' AND name='Villager'").fetchone()[0]
    durations = [r[0] for r in q("SELECT duration FROM match")]
    out.append(f"`time_s` is seconds since the match's first event. Villager Start→Complete in the log: "
               + ", ".join(f"{d} s × {n:,}" for d, n in deltas.most_common(3))
               + f". The 2023 train time is {villager_time} s in game time; in real time at the usual 1.7× game speed "
               f"it would take about {villager_time / 1.7:.0f} s. Match durations: median "
               f"{statistics.median(durations) / 60:.0f} min, which is plausible as game time. **Conclusion: timestamps "
               "are game time** (with the caveat that Start/Complete are simulated from a duration table, so this "
               "shows which clock the authors used, not an observation).")

    out.append("## Share of events with a game object")
    rows = q("""SELECT action, COALESCE(phase, '-'), COUNT(*), SUM(game_object_id IS NOT NULL),
                SUM(COALESCE(game_object_id = ?, 0)) FROM event GROUP BY 1, 2 ORDER BY 1, 2""", (info["unknown"],)).fetchall()
    out.append(md_table(["Action", "Phase", "Events", "With game_object_id", "Of these UNKNOWN"],
                        [(a, p, f"{n:,}", f"{m:,} ({pct(m, n)})", f"{u:,}") for a, p, n, m, u in rows]))
    total, mapped = q("SELECT COUNT(*), SUM(game_object_id IS NOT NULL) FROM event").fetchone()
    relevant, relevant_mapped = q("""SELECT COUNT(*), SUM(game_object_id IS NOT NULL AND game_object_id <> ?)
        FROM event WHERE action IN ('QUEUE','RESEARCH','BUILD')""", (info["unknown"],)).fetchone()
    out.append(f"All events: {pct(mapped, total)} have a game object. GATHER and SET_GATHER_POINT carry a resource "
               f"instead. QUEUE, RESEARCH and BUILD events: {pct(relevant_mapped, relevant)} map to a real game object "
               "(not UNKNOWN).")

    out.append("## Vocabulary")
    size, events = q("SELECT COUNT(DISTINCT game_object_id), COUNT(*) FROM event WHERE in_vocabulary=1").fetchone()
    by_action = q("""SELECT action, COUNT(DISTINCT game_object_id), COUNT(*) FROM event WHERE in_vocabulary=1
                     GROUP BY 1 ORDER BY 1""").fetchall()
    out.append(f"`in_vocabulary = 1` for COMMAND of QUEUE and RESEARCH and START of BUILD, without UNKNOWN: "
               f"**{size} distinct game objects**, {events:,} events. The 269 action types in the README become "
               f"{size} because the 4 placeholder types (`None`, empty suffix) go to UNKNOWN and are excluded.")
    out.append(md_table(["Action", "Distinct game objects", "Events"], [(a, d, f"{n:,}") for a, d, n in by_action]))
    top = q("""SELECT g.name, g.type, COUNT(*) FROM event e JOIN game_object g ON g.id = e.game_object_id
               WHERE e.in_vocabulary=1 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10""").fetchall()
    out.append("Most frequent tokens:\n\n" + md_table(["Object", "Type", "Events"], [(a, b, f"{n:,}") for a, b, n in top]))

    out.append("## Session flags")
    counts = info["command_counts"]
    out.append(md_table(["Flag", "Definition", "Sessions"], [
        ("short_match", f"match spans < {SHORT_MATCH_SECONDS} s (last minus first event)",
         q("SELECT SUM(short_match) FROM session").fetchone()[0]),
        ("no_commands", "no COMMAND event", q("SELECT SUM(no_commands) FROM session").fetchone()[0]),
        ("outlier_commands", f"more COMMAND events than the p{OUTLIER_PERCENTILE} of sessions with any "
         f"(nearest rank over {len(counts)} sessions): > **{info['threshold']}**",
         q("SELECT SUM(outlier_commands) FROM session").fetchone()[0]),
        ("has_unmapped_events", "a QUEUE, RESEARCH or BUILD event without a game object or with UNKNOWN",
         q("SELECT SUM(has_unmapped_events) FROM session").fetchone()[0]),
        ("any flag", "", q("""SELECT COUNT(*) FROM session WHERE short_match OR no_commands OR outlier_commands
                              OR has_unmapped_events""").fetchone()[0]),
    ]))
    out.append(f"COMMAND events per session (sessions with any): median {statistics.median(counts):.0f}, "
               f"p90 {counts[math.ceil(0.9 * len(counts)) - 1]}, p99 {info['threshold']}, max {counts[-1]}.")

    out.append("## Town Center builds and the estimated age")
    out.append("The age reached is estimated as the first `Command Research <Age>` plus the age's research time from "
               "the 2023 tree (Feudal 130 s, Castle 160 s, Imperial 190 s), never from `Complete Research`. It is an "
               "upper bound on how early the age could be reached: a queued age-up starts later than its command.")
    rows = q("""
        WITH age AS (SELECT e.session_id, MIN(e.time_s) + g.time AS castle_s FROM event e
                     JOIN game_object g ON g.id = e.game_object_id
                     WHERE e.phase='COMMAND' AND g.name='Castle Age' GROUP BY e.session_id),
             tc AS (SELECT e.session_id, e.time_s FROM event e JOIN game_object g ON g.id = e.game_object_id
                    WHERE e.action='BUILD' AND e.phase='START' AND g.name='Town Center'),
             start_tc AS (SELECT DISTINCT session_id FROM event WHERE ocel_type='Create Initial Town Center')
        SELECT CASE WHEN st.session_id IS NULL THEN 'no starting TC' ELSE 'starting TC' END,
               COUNT(*), SUM(age.castle_s IS NULL OR tc.time_s < age.castle_s)
        FROM tc LEFT JOIN age USING (session_id) LEFT JOIN start_tc st USING (session_id) GROUP BY 1""").fetchall()
    out.append(md_table(["Sessions", "TC builds", "Before the estimated Castle Age"],
                        [(a, f"{n:,}", f"{b:,} ({pct(b, n)})") for a, n, b in rows]))
    out.append("TC builds before Castle Age happen on maps without a starting TC (the first TC) and for Cumans "
               "(Feudal TC). Other early TC builds would be implausible under the 2023 rules. Phase 4 civ groups will "
               "show which of them are Cumans.")

    out.append(f"## Sample session (first {SAMPLE_MINUTES} minutes)")
    median = statistics.median(counts)
    sample = q("""SELECT s.id, s.source_id, COUNT(*) AS n FROM session s JOIN event e ON e.session_id = s.id
                  WHERE e.phase = 'COMMAND' AND NOT (s.short_match OR s.no_commands OR s.outlier_commands
                  OR s.has_unmapped_events) GROUP BY s.id ORDER BY ABS(COUNT(*) - ?), s.source_id LIMIT 1""",
               (median,)).fetchone()
    out.append(f"Session `{sample[1]}`: the unflagged session whose COMMAND count ({sample[2]}) is closest to the "
               f"median. ✓ marks vocabulary events, i.e. what the model predicts.")
    events = q("""SELECT e.time_s, e.action, e.phase,
                         COALESCE(g.name, CASE WHEN e.action = 'CREATE_INITIAL'
                                  THEN '(' || substr(e.ocel_type, 16) || ', not a game object)' ELSE '' END),
                         e.resource, e.in_vocabulary, e.ocel_type
                  FROM event e LEFT JOIN game_object g ON g.id = e.game_object_id
                  WHERE e.session_id = ? AND e.time_s < ? ORDER BY e.time_s, e.seq""",
               (sample[0], SAMPLE_MINUTES * 60)).fetchall()
    decisions = [(mmss(t), a, n or r, "✓" if v else "") for t, a, p, n, r, v, _ in events
                 if v or a == "CREATE_INITIAL" or p == "COMMAND"]
    out.append("Player decisions and starting objects:\n\n" + md_table(["Time", "Action", "Object", "Vocab"], decisions))
    out.append(fold(f"All {len(events)} events of the first {SAMPLE_MINUTES} minutes", md_table(
        ["Time", "Action", "Phase", "Object", "Resource", "Vocab", "ocel_type"],
        [(mmss(t), a, p, n, r, "✓" if v else "", o) for t, a, p, n, r, v, o in events])))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n\n".join(out) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Load the replay events into aoe2.db.")
    parser.add_argument("--ocel", type=Path, default=DEFAULT_OCEL)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    for path in (args.ocel, args.db):
        if not path.exists():
            sys.exit(f"Missing {path}" + (" (run scripts/03_load_static.py first)" if path == args.db else ""))
    started = time.time()
    source_before = args.ocel.stat()
    con = sqlite3.connect(args.db, uri=True)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA synchronous = OFF")
    con.execute("PRAGMA journal_mode = MEMORY")
    con.execute("PRAGMA cache_size = -512000")
    con.execute("ATTACH DATABASE ? AS src", (f"file:{args.ocel.resolve()}?mode=ro",))
    with con:
        info = load(con, started)
    write_report(con, info, time.time() - started)
    con.close()
    after = args.ocel.stat()
    if (source_before.st_mtime_ns, source_before.st_size) != (after.st_mtime_ns, after.st_size):
        sys.exit("The source file changed during the run. This should be impossible with mode=ro.")
    step(f"wrote {REPORT}; source file unchanged", started)


if __name__ == "__main__":
    main()
