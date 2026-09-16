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

Local disk only. No network call, no credential of any kind, nothing that
leaves the session this skill is running in. An earlier version of this
script optionally pushed each entry straight to GitHub through a
self-hosted relay service, so finds could sync across devices without the
skill ever holding a GitHub credential directly. That push path was
removed (September 2026) -- it was nice to have but not needed in
practice, and it was also the source of two separate rounds of a Claude
session correctly declining to touch either the auto-push behavior or the
live relay credential that made it work. Local-only logging sidesteps
both problems by not having a credential or a network call anywhere in
this script for anything to go wrong with. If cross-device sync for finds
comes back later, it should start from that history rather than repeat it.

CLI (for manual testing -- normally called with a pre-built dict, not from
the command line):
  python3 log_find.py '{"photo_date": "2026-05-24", ...}'

Import and call log_find(entry_dict) directly from the skill workflow.
"""

import sys
import json
import os
import datetime

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
LOG_FILE = os.path.join(LOGS_DIR, 'finds_log.jsonl')

REQUIRED_FIELDS = ('photo_date', 'top_candidates')


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
    a result dict with the new total line count, or raises -- this is
    local disk I/O only, nothing here should fail silently the way a
    network call might."""
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        raise ValueError(f'log entry missing required field(s): {missing}')

    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry) + '\n')

    with open(LOG_FILE) as f:
        line_count = sum(1 for _ in f)

    return {
        'ok': True,
        'log_file': LOG_FILE,
        'total_entries': line_count,
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
        result = log_find(entry)
    except ValueError as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)

    print(json.dumps(result))


if __name__ == '__main__':
    main()
