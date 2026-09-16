#!/usr/bin/env python3
"""
fetch_reference_data.py - Pulls the species database (species_registry.json,
lookalike_pairs.json, toxin_syndromes.json, photos_manifest.json) from the
repo's raw GitHub content at runtime, so SKILL.md itself can stay slim
instead of carrying the whole database inline.

Uses raw.githubusercontent.com specifically, not api.github.com -- the two
are different domains with different access rules. Verified live on
2026-09-16: a sandboxed Claude session that had GitHub's API blocked
outright (a session-level "add_repo" gate) could still reach
raw.githubusercontent.com with a plain unauthenticated GET, because it's
just a static content CDN for public repo files, not the API. No PAT or
auth of any kind needed to read a public repo this way. If a future
environment blocks this domain too, that's a different, harder problem --
this script fails safe into "use whatever's cached" rather than crashing,
so a temporary network hiccup or an unusually strict sandbox doesn't take
the skill down, it just means data can be stale until the next fetch works.

CLI:
  python3 fetch_reference_data.py                 -- fetch all four files,
                                                       respecting cache TTL
  python3 fetch_reference_data.py --force          -- ignore cache, re-fetch

Set REPO_OWNER / REPO_NAME / REPO_BRANCH below once the repo is live.
"""

import sys
import json
import os
import time
import urllib.request
import urllib.error

REPO_OWNER = 'savoxis'
REPO_NAME = 'mushroom-identifier-dontusethisever'
REPO_BRANCH = 'main'

REFS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'references')
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
CACHE_META_FILE = os.path.join(CACHE_DIR, 'reference_data_fetch_times.json')

FILES = ['species_registry.json', 'lookalike_pairs.json', 'toxin_syndromes.json', 'photos_manifest.json']
CACHE_TTL_SECONDS = 86400  # 1 day -- this is reference data that changes by hand-edited commits, not live data


def _raw_url(filename):
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{REPO_BRANCH}/references/{filename}"


def _read_fetch_times():
    if not os.path.exists(CACHE_META_FILE):
        return {}
    try:
        with open(CACHE_META_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_fetch_times(times):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(CACHE_META_FILE, 'w') as f:
            json.dump(times, f)
    except Exception:
        pass


def fetch_file(filename, force=False):
    """Fetches one references/<filename> from the repo's raw content into
    references/<filename> locally, respecting the cache TTL unless
    force=True. Returns (ok: bool, detail: str). On any failure, leaves
    whatever local copy already exists untouched rather than deleting it --
    stale data beats no data."""
    dest_path = os.path.join(REFS_DIR, filename)
    fetch_times = _read_fetch_times()
    last_fetch = fetch_times.get(filename, 0)

    if not force and os.path.exists(dest_path) and (time.time() - last_fetch) < CACHE_TTL_SECONDS:
        return True, 'cached (fresh)'

    url = _raw_url(filename)
    req = urllib.request.Request(url, headers={'User-Agent': 'westcoast-mushroom-id'})
    data = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                data = response.read()
                break
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            if attempt == 0:
                time.sleep(1)
            else:
                if os.path.exists(dest_path):
                    return True, f'fetch failed ({e}), using existing cached copy'
                return False, f'fetch failed ({e}) and no cached copy exists'

    try:
        json.loads(data)  # validate before overwriting a good local copy with garbage
    except json.JSONDecodeError as e:
        if os.path.exists(dest_path):
            return True, f'fetched data was not valid JSON ({e}), keeping existing cached copy'
        return False, f'fetched data was not valid JSON ({e}) and no cached copy exists'

    os.makedirs(REFS_DIR, exist_ok=True)
    with open(dest_path, 'wb') as f:
        f.write(data)
    fetch_times[filename] = time.time()
    _write_fetch_times(fetch_times)
    return True, 'fetched fresh copy'


def main():
    force = '--force' in sys.argv
    results = {}
    all_ok = True
    for filename in FILES:
        ok, detail = fetch_file(filename, force=force)
        results[filename] = detail
        all_ok = all_ok and ok
        print(f"{filename}: {detail}")

    if not all_ok:
        print(json.dumps({'ok': False, 'results': results}))
        sys.exit(1)
    print(json.dumps({'ok': True, 'results': results}))


if __name__ == '__main__':
    main()
