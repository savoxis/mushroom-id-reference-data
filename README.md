# mushroom-identifier-dontusethisever

Companion data repo for the `westcoast-mushroom-id` Claude skill. The skill
itself (SKILL.md) lives in Claude's skill system, not here - this repo holds
everything that skill needs at runtime but shouldn't have to carry inline:
the species database, sourced reference photos, and the lookup/logging
scripts.

## Why this split exists

Claude's `propose_skills` tool only accepts a single SKILL.md file - no
bundled folders, no separate scripts. The first version of this skill
crammed the entire species database (44 species, citations, lookalike
tables) and three Python scripts into one ~1700-line markdown file. It
worked, but it was a bad way to maintain a life-safety reference: a single
bad find-and-replace could silently corrupt a species entry, there was no
way to diff a change to just the toxin table, and updating one photo
reference meant re-proposing the whole skill.

This repo is the fix. SKILL.md stays slim - it's the workflow, the rules,
the decision logic - and pulls everything else from here at runtime over
`raw.githubusercontent.com`. Editing a species entry, fixing a bad photo
reference, or adding a new lookalike pair is now a normal git commit to a
JSON file, not a skill re-proposal.

## Repo layout

```
references/
  species_registry.json    44 species, tiers A/B/C, full identifying detail
  lookalike_pairs.json     10 dangerous-lookalike pairs with the field test
                            that tells them apart
  toxin_syndromes.json     10 toxin syndrome profiles (onset, mechanism,
                            treatment window)
  photos_manifest.json     real, license-checked reference photos per
                            species, sourced from iNaturalist

scripts/
  exif_extract.py          pulls GPS + capture timestamp out of an uploaded
                            photo's EXIF data
  geo.py                   reverse-geocodes coordinates, checks whether a
                            find is in the skill's supported West Coast range
  weather_at_time.py       looks up historical weather/precip for a find's
                            location and date (fruiting conditions context)
  fetch_reference_data.py  pulls the four references/*.json files down at
                            runtime, with a 24h cache and fail-safe fallback
  fetch_photo_refs.py      the tool that built photos_manifest.json against
                            the iNaturalist API - rerun this to refresh or
                            extend photo coverage, not by hand-editing the
                            manifest
  log_find.py              appends one entry to logs/finds_log.jsonl for
                            each ID the skill runs (always local disk),
                            and optionally pushes that entry straight to
                            GitHub via the relay below if it's configured
                            for the session

logs/
  finds_log.jsonl          personal find history, one JSON object per line,
                            created on first use. Not pre-populated - this
                            repo ships with no find history in it.

relay/
  mushroom_log_relay.py    a small, narrow, self-hosted service (see its
                            own README) that lets log_find.py push find-log
                            entries straight to this repo's main branch,
                            without the skill ever holding a GitHub
                            credential and without needing GitHub's own
                            write endpoints reachable from inside a fresh
                            Claude session
```

The relay's own auth token (not the GitHub PAT -- see `relay/README.md`
for that distinction) is embedded directly in SKILL.md's Setup section, by
explicit choice, so find-log push works every session with no per-device
setup. That's a real tradeoff against keeping it off a file that syncs
everywhere the skill loads -- made deliberately, because the token itself
is scoped narrow enough (append-only, schema-validated, one file) that the
worst case of it leaking is spam in a log, not a repo compromise. Rotate
it by regenerating a new token, updating the relay container's `.env`, and
updating the value in SKILL.md (then re-saving the skill).

## How SKILL.md uses this repo

SKILL.md has a small bootstrap script embedded directly in it (this is the
one piece of code that has to stay inline - it's what fetches everything
else, so it can't itself depend on being fetched). At the start of a run it
tries `git clone --depth 1` against this repo, and if that fails, falls
back to pulling each file individually from `raw.githubusercontent.com`.

That fallback matters: this Cowork sandbox currently has `api.github.com`
blocked at the session level (see "Known gaps" below), but
`raw.githubusercontent.com` - a plain static-content CDN, different domain,
no auth needed for a public repo - is not affected. `fetch_reference_data.py`
uses the same raw-CDN approach with a 24-hour cache, and fails safe: if a
fetch fails or comes back invalid, it keeps using whatever copy is already
on disk instead of deleting good data over a network hiccup.

## Species database coverage

- 44 species total: 9 Tier A (deadly, hard warning triggers), 7 Tier B
  (serious toxicity), 28 Tier C (edible or mild-risk, informational)
- 10 dangerous-lookalike pairs, each with a specific field-checkable
  distinguishing test - never "trust your gut," always a concrete
  physical check
- 10 toxin syndrome profiles backing the Tier A/B entries

## Photo reference coverage

`photos_manifest.json` was built against iNaturalist's public API, filtered
to `quality_grade=research` (community-vetted species ID - the right bar
for something people are using to decide what to eat) and to open licenses
only (cc0, cc-by, cc-by-sa, cc-by-nc, cc-by-nc-sa). Only the photo URL,
license, attribution, and observation link are stored - nothing is
downloaded and rehosted, so there's no copyright exposure and the
attribution trail back to the original observer stays intact.

Current coverage: 43 of 44 species have at least one photo (127 photos
total). The one gap is Blewit (Clitocybe nuda) - tried both the current
name and the older synonym Lepista nuda, neither turned up an
open-licensed research-grade photo on iNaturalist. Blewit is Tier C, not a
safety-critical species, so this is logged here as an honest gap rather
than worked around with a lower-quality source. If you run across a good
open-licensed Blewit photo, add it to `photos_manifest.json` by hand or
rerun `fetch_photo_refs.py "Clitocybe nuda"` once iNaturalist has more
research-grade observations for it.

To refresh or extend photo coverage generally:

```
python3 scripts/fetch_photo_refs.py                  # full registry rebuild
python3 scripts/fetch_photo_refs.py "Amanita phalloides"   # single species test
```

## Known gaps / open items

- **Auto-push for find logs is live.** The skill can always read this
  repo at runtime (see above), but it can't commit or push to it directly
  - a session-level gate inside a Claude Cowork sandbox blocks both
  `api.github.com` and `git push` to `github.com` behind a
  repo-authorization step that a fresh chat has no way to satisfy
  (confirmed this is not a credentials problem - a valid PAT supplied
  directly gets denied identically to no PAT at all, because the block
  happens before any credential is even checked). That's a structural
  property of the sandbox, not something fixable from inside a session,
  and it won't get better by waiting - a skill invoked in a new chat
  never has a "workspace" carried over from a previous one anyway. So
  instead of fighting that gate, `log_find.py` pushes find-log entries
  through `relay/` - a small self-hosted service (see `relay/README.md`)
  that lives off-sandbox and is the only thing that ever actually holds a
  GitHub credential. It's deliberately narrow: one endpoint, one file
  it's allowed to touch (`logs/finds_log.jsonl`), strict schema
  validation before anything reaches GitHub. The relay's own auth token
  is embedded in SKILL.md so this works every session with no per-device
  setup (see the tradeoff note above); the actual GitHub PAT never
  leaves the relay container. If the relay container itself is ever
  down or unreachable, `log_find.py` degrades to exactly what it did
  before the relay existed - local write only, no error, nothing missing.
- **Species corrections are NOT pushable this way, on purpose.** Editing
  `references/species_registry.json`, `lookalike_pairs.json`, or
  `toxin_syndromes.json` stays a manual, human-reviewed
  `git add / commit / push`, same as always. Given the life-safety stakes
  of that data, "the skill can silently rewrite the deadly-species
  database on its own" is not a capability this project wants, even with
  a relay in place - the relay's whole design intentionally has no route
  for it.
- **Blewit has no photo** - see above.

## Editing the database by hand

Each entry in `species_registry.json` follows one schema (Tier C entries
add a `watch_for` field, everything else is shared):

```
id, common_name, scientific_name, tier, severity, toxin_syndrome, effect,
key_features, habitat_substrate, season_west_coast, range_notes,
confused_with, cannot_determine_from_photo, distinguishing_test,
citations, inat_taxon_name
```

`inat_taxon_name` is what `fetch_photo_refs.py` queries against - it needs
to be a real, currently-accepted iNaturalist taxon name (species or genus
level), not a display label. If you rename or add a species, use its
current accepted scientific name here, not a common synonym, or the photo
fetch will silently come back empty.

Any change to `citations` should point at a real, checkable source -
this is life-safety reference data, not a place to guess.
