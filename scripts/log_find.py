#!/usr/bin/env python3
"""
log_find.py - Appends one entry to logs/finds_log.jsonl: a personal record
of an ID the skill ran, for later lookback (what did I find last October,
what did I call that thing behind the shed last year).

Format is JSON Lines (one JSON object per line) rather than a single JSON
array or one file per find, on purpose -- appending a line is a one-line
git diff and never risks corrupting or merge-conflicting the rest of the
log the way rewriting a big array or a single nested file can. This script
only ever appends; it never rewrites existing lines.

The local append always happens and never depends on network access -- see
log_find() below. Separately, if MUSHROOM_LOG_RELAY_URL and
MUSHROOM_LOG_RELAY_TOKEN are set in the environment, this also POSTs the
entry to a self-hosted relay (see ../relay/README.md) that commits it
straight to the repo's logs/finds_log.jsonl on GitHub. With neither
variable set, this behaves exactly as if the relay didn't exist: local
write only, no network call, no error.

This script itself has no way to know whether the user actually agreed to
a GitHub push this session -- that's a conversation the skill has with the
user, not something this script can check. SKILL.md's workflow only sets
these two environment variables after asking the user, once per session,
whether they want finds pushed or kept local -- this script trusts that
gate and pushes whenever it sees both variables set. Anything that calls
this script directly (manual testing, another tool) needs to apply that
same gate itself rather than setting the variables unconditionally. Use
log_find_and_push(entry) from the skill workflow, which does both steps
and reports honestly on each; log_find(entry) alone is still here for
anything that only wants the local write.

CLI (for manual testing -- normally called with a pre-built dict, not from
the command line):
  python3 log_find.py '{"photo_date": "2026-05-24", ...}'

Import and call log_find_and_push(entry_dict) directly from the skill
workflow.
"""

import sys
import json
import os
import datetime
import urllib.request
import urllib.error

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
LOG_FILE = os.path.join(LOGS_DIR, 'finds_log.jsonl')

REQUIRED_FIELDS = ('photo_date', 'top_candidates')

RELAY_URL = os.environ.get('MUSHROOM_LOG_RELAY_URL')
RELAY_TOKEN = os.environ.get('MUSHROOM_LOG_RELAY_TOKEN')


def build_entry(photo_date, top_candidates, location=None, hard_warning_species=None, notes=None):
    """Constructs one log entry. top_candidates is a list of dicts, each
    with at minimum species_id, common_name, tier, confidence_band --
    matching what the skill already produces for the [TOP CANDIDATES]
    section of an answer, so this should just be that list passed straight
    through, not re-typed."""
    return {
        'logged_at': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'photo_date': photo_date,
        'location': location,  # {'lat':.., 'lon':.., 'matched_name':.., 'in_scope':..} or None if never resolved
        'top_candidates': top_candidates,
        'hard_warning_triggered': bool(hard_warning_species),
        'hard_warning_species': hard_warning_species or [],
        'notes': notes,
    }


def log_find(entry):
    """Appends entry (a dict, normally from build_entry) as one line to
    logs/finds_log.jsonl. Creates the file and logs/ dir if needed. Returns
    the line count after the append, or raises -- this is local disk I/O
    only, nothing here should fail silently the way a network call might."""
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        raise ValueError(f'log entry missing required field(s): {missing}')

    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry) + '\n')

    with open(LOG_FILE) as f:
        return sum(1 for _ in f)


def push_to_relay(entry):
    """Best-effort push of entry to the self-hosted relay, if configured.
    Returns (attempted: bool, ok: bool, detail: str). Never raises -- a
    relay outage, bad token, DNS failure, or the relay simply not being
    set up for this session are all the same case from the skill's point
    of view: the local write already succeeded, this is a bonus, not a
    dependency."""
    if not RELAY_URL or not RELAY_TOKEN:
        return False, False, 'relay not configured for this session (MUSHROOM_LOG_RELAY_URL / MUSHROOM_LOG_RELAY_TOKEN not set) -- logged locally only'

    url = RELAY_URL.rstrip('/') + '/log-find'
    data = json.dumps(entry).encode()
    req = urllib.request.Request(url, data=data, method='POST', headers={
        'Authorization': f'Bearer {RELAY_TOKEN}',
        'Content-Type': 'application/json',
        'User-Agent': 'westcoast-mushroom-id',
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
            return True, True, f"pushed to GitHub, commit {body.get('commit_sha', '?')[:8]}"
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get('error', str(e))
        except Exception:
            detail = str(e)
        return True, False, f'relay rejected the push (HTTP {e.code}): {detail}'
    except Exception as e:
        return True, False, f'relay unreachable: {e}'


def log_find_and_push(entry):
    """The function the skill workflow should actually call. Always does
    the local append first (raises on that failing, same as log_find --
    a bad entry shouldn't silently vanish). Then, only if that succeeded,
    makes a best-effort attempt to push to the relay and reports the
    outcome either way rather than staying quiet about it."""
    line_count = log_find(entry)
    relay_attempted, relay_ok, relay_detail = push_to_relay(entry)
    return {
        'ok': True,
        'log_file': LOG_FILE,
        'total_entries': line_count,
        'relay_attempted': relay_attempted,
        'relay_ok': relay_ok,
        'relay_detail': relay_detail,
    }


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: log_find.py \'<json entry or build_entry kwargs>\''}))
        sys.exit(1)
    try:
        entry = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'invalid JSON argument: {e}'}))
        sys.exit(1)

    if 'logged_at' not in entry:
        entry = build_entry(
            photo_date=entry.get('photo_date'),
            top_candidates=entry.get('top_candidates', []),
            location=entry.get('location'),
            hard_warning_species=entry.get('hard_warning_species'),
            notes=entry.get('notes'),
        )

    try:
        result = log_find_and_push(entry)
    except ValueError as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)

    print(json.dumps(result))


if __name__ == '__main__':
    main()
