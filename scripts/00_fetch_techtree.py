#!/usr/bin/env python3
"""Download the aoe2techtree rule files in two versions.

2023 (primary): the aoe2techtree repository at commit TECHTREE_2023_COMMIT
(9d2f8987b2852695e997cc8ca4bee077108d1fbc, 2023-12-15, the last commit of 2023 and
the end of the replay period). Files: data/data.json, data/locales/en/strings.json
and js/techtree.js (tree layout with producers and prerequisite links; parsed,
never executed). Stored in data/raw/aoe2techtree_2023/. That commit has no per-civ
tree files; the 2023 data.json carries per-civ ages in `techtrees` instead.

2026 (fallback): the live site's string table and per-civ tree files, matching the
committed data/raw/data.json. Stored in data/raw/aoe2techtree/.

Each folder gets a manifest (URL, SHA-256, date). Files that already exist are kept
unless you pass --force. The script also reports whether the site's data.json still
matches the local data/raw/data.json, because the 2026 strings and trees must match it.

Usage: python3 scripts/00_fetch_techtree.py [--force]
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "aoe2-next-action-prediction/1.0"

TECHTREE_2023_COMMIT = "9d2f8987b2852695e997cc8ca4bee077108d1fbc"
RAW_2023 = f"https://raw.githubusercontent.com/SiegeEngineers/aoe2techtree/{TECHTREE_2023_COMMIT}"
OUT_DIR_2023 = ROOT / "data/raw/aoe2techtree_2023"
FILES_2023 = {  # local name -> path in the repository at that commit
    "data.json": "data/data.json",
    "strings_en.json": "data/locales/en/strings.json",
    "techtree.js": "js/techtree.js",
}

BASE_URL_2026 = "https://aoe2techtree.net/data"
DATA_JSON_2026 = ROOT / "data/raw/data.json"
OUT_DIR_2026 = ROOT / "data/raw/aoe2techtree"


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def fetch_all(targets, out_dir, force, manifest_header):
    """Download missing targets into out_dir and write its manifest when anything changed."""
    fetched = 0
    for relative_path, url in targets.items():
        path = out_dir / relative_path
        if path.exists() and not force:
            continue
        body = download(url)
        if relative_path.endswith(".json"):
            json.loads(body)  # refuse to store anything that is not JSON
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        fetched += 1
    manifest_path = out_dir / "manifest.json"
    if fetched or not manifest_path.exists():
        manifest = {
            **manifest_header,
            "fetched_on": dt.date.today().isoformat(),
            "files": {
                relative_path: {
                    "url": url,
                    "sha256": hashlib.sha256((out_dir / relative_path).read_bytes()).hexdigest(),
                }
                for relative_path, url in targets.items()
            },
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"{fetched} file(s) downloaded, {len(targets) - fetched} already present, in {out_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="re-download files that already exist")
    args = parser.parse_args()

    fetch_all({name: f"{RAW_2023}/{path}" for name, path in FILES_2023.items()}, OUT_DIR_2023, args.force,
              {"source": "github.com/SiegeEngineers/aoe2techtree", "commit": TECHTREE_2023_COMMIT})

    if not DATA_JSON_2026.exists():
        sys.exit(f"Missing {DATA_JSON_2026}")
    local_data = DATA_JSON_2026.read_bytes()
    targets = {"strings_en.json": f"{BASE_URL_2026}/locales/en/strings.json"}
    for civ in sorted(json.loads(local_data)["civs"]):
        targets[f"trees/{civ.upper()}.json"] = f"{BASE_URL_2026}/trees/{civ.upper()}.json"
    fetch_all(targets, OUT_DIR_2026, args.force,
              {"source": BASE_URL_2026, "data_json_sha256": hashlib.sha256(local_data).hexdigest()})

    if download(f"{BASE_URL_2026}/data.json") == local_data:
        print("The site's data.json is byte-identical to data/raw/data.json.")
    else:
        print("WARNING: the site's data.json differs from data/raw/data.json. The 2026 snapshot "
              "may be from another version than the local file.", file=sys.stderr)


if __name__ == "__main__":
    main()
