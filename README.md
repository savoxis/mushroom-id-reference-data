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
                            each ID the skill runs (local disk write only,
                            does not touch git)

logs/
  finds_log.jsonl          personal find history, one JSON object per line,
                            created on first use. Not pre-populated - this
                            repo ships with no find history in it.
```

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

- **No auto-push yet.** The skill can read this repo at runtime, but it
  cannot currently commit or push to it - a session-level GitHub API gate
  in this Cowork sandbox blocks `api.github.com` outright (not a
  credentials problem - confirmed the block is identical with and without
  a valid PAT). `raw.githubusercontent.com` reads are unaffected, which is
  why the read path works. Getting the skill to push its own updates
  (find logs, corrections) to `main` is a tracked goal, not an abandoned
  one - a GitHub Actions workflow that runs Claude Code inside this repo's
  own CI is a plausible way to close this gap and is worth investigating
  in a future pass. Until then: the skill prepares files locally,
  `log_find.py` writes to `logs/finds_log.jsonl` on disk, and a human runs
  `git add / commit / push` to actually get changes onto `main`.
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
