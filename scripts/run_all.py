#!/usr/bin/env python3
"""Rebuild data/processed/aoe2.db by running the build scripts in order.

03_load_static.py applies the schema and so wipes all replay data. Running it alone
leaves an empty event table. This script runs 03 and every later build script that
exists, stops at the first failure and prints the runtime of each step.

Usage: .venv/bin/python scripts/run_all.py [--ocel PATH]
(08_export.py needs pyarrow from requirements.txt; everything else uses the standard library.)
Not included: 00_fetch_techtree.py (downloads, only needed once) and 01_explore.py
(writes the exploration report and does not touch the database).
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
STEPS = ["03_load_static.py", "04_load_replays.py", "05_civ_inference.py", "06_resources.py", "07_validate.py",
         "08_export.py"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ocel", help="path to the OCEL SQLite file (passed to the scripts that read it)")
    args = parser.parse_args()

    timings = []
    for step in STEPS:
        path = SCRIPTS / step
        if not path.exists():
            print(f"skip {step} (not written yet)")
            continue
        command = [sys.executable, str(path)]
        if args.ocel and step in ("03_load_static.py", "04_load_replays.py", "07_validate.py"):
            command += ["--ocel", args.ocel]
        print(f"run  {step}", flush=True)
        started = time.time()
        result = subprocess.run(command)
        timings.append((step, time.time() - started))
        if result.returncode != 0:
            sys.exit(f"{step} failed with exit code {result.returncode}; later steps were not run")
    print("\n".join(f"{step:22} {seconds:6.0f} s" for step, seconds in timings))


if __name__ == "__main__":
    main()
