#!/usr/bin/env python3
"""Export the training data for the next-action model into data/processed/.

  decisions.parquet  one row per in-vocabulary decision, in session order
  vocab.csv          token_id, game_object_id, name, type, action, count
  split.csv          train/val/test = 80/10/10 by match (both players in the same split)

Nothing is excluded and repetitions are not collapsed (e.g. runs of Villager): the session
flags are columns, so filtering and deduplication are model-side choices. Token ids start
at 0 and are ordered by frequency; no special tokens (PAD, BOS, ...) are reserved.

Needs pyarrow (see requirements.txt).
Usage: python3 scripts/08_export.py [--db PATH] [--seed N]
"""
import argparse
import csv
import random
import sqlite3
import sys
import time
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    sys.exit("pyarrow is missing: pip install -r requirements.txt (e.g. into .venv) and run with that Python.")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data/processed/aoe2.db"
OUT = ROOT / "data/processed"
SPLITS = (("train", 0.8), ("val", 0.1), ("test", 0.1))
SEED = 42


def main():
    parser = argparse.ArgumentParser(description="Export decisions, vocabulary and split for training.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    started = time.time()
    con = sqlite3.connect(args.db)
    if con.execute("SELECT COUNT(*) FROM resource_snapshot").fetchone()[0] == 0:
        sys.exit("resource_snapshot is empty. Run scripts/run_all.py first.")

    vocab = con.execute("""SELECT e.game_object_id, g.name, g.type, MIN(e.action), COUNT(*) FROM event e
                           JOIN game_object g ON g.id = e.game_object_id WHERE e.in_vocabulary = 1
                           GROUP BY e.game_object_id ORDER BY COUNT(*) DESC, e.game_object_id""").fetchall()
    token = {row[0]: i for i, row in enumerate(vocab)}
    with open(OUT / "vocab.csv", "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["token_id", "game_object_id", "name", "type", "action", "count"])
        writer.writerows((token[o], o, n, t, a, c) for o, n, t, a, c in vocab)

    rows = con.execute("""
        SELECT e.id, s.id, s.source_id, m.id, m.source_id, e.game_object_id, g.name, e.action, e.time_s,
               r.age, r.age_source, s.civ_group_id,
               s.short_match, s.no_commands, s.outlier_commands, s.has_unmapped_events
        FROM event e JOIN session s ON s.id = e.session_id JOIN match m ON m.id = s.match_id
        JOIN game_object g ON g.id = e.game_object_id JOIN resource_snapshot r ON r.event_id = e.id
        WHERE e.in_vocabulary = 1 ORDER BY s.id, e.id""").fetchall()
    columns = {k: [] for k in ("event_id", "session_id", "session_source_id", "match_id", "match_source_id", "seq",
                               "token_id", "game_object_id", "object", "action", "time_s", "minute", "age",
                               "age_source", "civ_group_id", "short_match", "no_commands", "outlier_commands",
                               "has_unmapped_events")}
    previous_session, seq, repeats, previous_token = None, 0, 0, None
    for (event_id, session_id, session_ref, match_id, match_ref, obj, name, action, t, age, age_source, group,
         short, no_cmd, outlier, unmapped) in rows:
        if session_id != previous_session:
            previous_session, seq, previous_token = session_id, 0, None
        repeats += token[obj] == previous_token
        previous_token = token[obj]
        values = (event_id, session_id, session_ref, match_id, match_ref, seq, token[obj], obj, name, action, t,
                  t // 60, age, age_source, group, bool(short), bool(no_cmd), bool(outlier), bool(unmapped))
        for key, value in zip(columns, values):
            columns[key].append(value)
        seq += 1
    types = {"event_id": pa.int64(), "session_id": pa.int32(), "match_id": pa.int32(), "seq": pa.int32(),
             "token_id": pa.int16(), "game_object_id": pa.int32(), "time_s": pa.int32(), "minute": pa.int16(),
             "age": pa.int8(), "civ_group_id": pa.int32()}
    table = pa.table({k: pa.array(v, type=types.get(k)) for k, v in columns.items()})
    pq.write_table(table, OUT / "decisions.parquet", compression="zstd")

    matches = [r for r in con.execute("SELECT id, source_id FROM match ORDER BY id")]
    random.Random(args.seed).shuffle(matches)
    bounds, start = [], 0
    for name, share in SPLITS:
        end = start + round(share * len(matches))
        bounds.append((name, start, end))
        start = end
    with open(OUT / "split.csv", "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["match_id", "match_source_id", "split"])
        assigned = []
        for name, a, b in bounds:
            assigned += [(m, ref, name) for m, ref in matches[a:b if name != "test" else len(matches)]]
        writer.writerows(sorted(assigned))
    con.close()
    counts = {n: sum(1 for *_, s in assigned if s == n) for n, _ in SPLITS}
    print(f"decisions.parquet: {len(rows):,} rows; vocab.csv: {len(vocab)} tokens; split.csv: {counts} matches; "
          f"consecutive repeats within a session: {repeats:,} ({100 * repeats / len(rows):.1f}%); "
          f"{time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
