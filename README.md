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
  species_registry.json    58 species, tiers A/B/C, full identifying detail
                            -- Tier C (edible/informational) entries carry
                            deep-ID fields (spore print, cap/stem size,
                            flesh/latex behavior) on top of the shared schema
  lookalike_pairs.json     14 dangerous-lookalike pairs with the field test
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
                            two independent sources -- iNaturalist directly,
                            and Mushroom Observer's collection by way of
                            GBIF (see the script's own docstring for why
                            GBIF and not Mushroom Observer's API directly)
                            - rerun this to refresh or extend photo
                            coverage, not by hand-editing the manifest
  log_find.py              appends one entry to logs/finds_log.jsonl for
                            each ID the skill runs -- local disk only, no
                            network call, no credential of any kind

logs/
  finds_log.jsonl          personal find history, one JSON object per line,
                            created on first use. Not pre-populated - this
                            repo ships with no find history in it.
```

Find logs are local-only, on purpose. An earlier version of this project
had `log_find.py` optionally push each entry straight to this repo's
`main` branch through a small self-hosted relay service, specifically to
avoid the skill ever holding a GitHub credential directly. That went
through two rounds of trouble -- a session correctly declined to
auto-export the relay's token and push location data without asking, and
a later fix that added a consent step but kept the token embedded in
SKILL.md got a second session correctly treating the whole file as
suspect over the live secret sitting in it. Removing the push feature
entirely (September 2026) sidesteps both problems at the root instead of
patching around them again -- there's no credential in this project
anywhere now, and nothing for a session to need to trust or distrust. If
GitHub push for finds ever comes back, it should start from that history,
not repeat it.

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

- 58 species total: 9 Tier A (deadly, hard warning triggers), 11 Tier B
  (serious toxicity, including one -- Verpa bohemica -- whose safety is
  genuinely disputed rather than confirmed either way), 38 Tier C (edible
  or mild-risk, informational)
- 14 dangerous-lookalike pairs, each with a specific field-checkable
  distinguishing test - never "trust your gut," always a concrete
  physical check
- 10 toxin syndrome profiles backing the Tier A/B entries (Verpa bohemica's
  disputed status deliberately isn't forced into a confirmed-toxin row --
  see its entry in `species_registry.json`)

### Tier C deep-ID fields (added September 2026)

Every Tier C (edible/informational) entry now carries four extra fields
beyond the shared schema, on top of `watch_for`:

- `spore_print_color` -- the single most reliable non-destructive field
  check most foragers skip
- `cap_size` / `stem_size` -- real dimension ranges, not just qualitative
  "large" or "small"
- `flesh_characteristics` -- what happens when you cut it: staining,
  bruising, latex color and behavior on air exposure (this is the field
  that actually rules milk caps and blue-staining boletes in or out)

These came out of a research pass across MushroomExpert.com, MykoWeb
California Fungi, First Nature, NAMA, Pacific Northwest Key Council trial
keys, and regional mycological society pages -- not guessed. Where sources
disagreed on a range (which happens more than field guides let on), the
entry says so instead of picking one number and presenting it as settled.
Two entries also got corrected in the process: Lion's Mane (Hericium
erinaceus) and Bear's Head Tooth (Hericium abietis) used to be lumped
together as one entry -- they're genuinely different species (hardwood vs
conifer host, unbranched vs branched structure) and now have separate
entries with a field check to tell them apart.

## Photo reference coverage

`photos_manifest.json` is built against two independent sources, both
queried by `fetch_photo_refs.py`:

- **iNaturalist**, filtered to `quality_grade=research` (community-vetted
  species ID - the right bar for something people are using to decide what
  to eat)
- **Mushroom Observer**, reached indirectly through GBIF rather than MO's
  own API - `mushroomobserver.org` itself is not reachable from this
  project's usual build environment (verified live: every connection gets
  reset at the TLS handshake, while every other host this project uses
  connects fine), but MO publishes its collection to GBIF, and GBIF is
  reachable. Same underlying photos and licenses, just fetched through a
  mirror. See the script's own docstring for the full reasoning, in case
  MO's API becomes directly reachable from wherever this is run later.

Both sources are filtered to open licenses only (cc0, cc-by, cc-by-sa,
cc-by-nc, cc-by-nc-sa). Only the photo URL, license, attribution, and
observation link are stored - nothing is downloaded and rehosted, so
there's no copyright exposure and the attribution trail back to the
original observer stays intact. iNaturalist is queried first for each
species; Mushroom Observer tops up any remaining slots up to 3 photos per
species, not because one source is more trustworthy than the other, but
because iNaturalist's quality_grade filter is a stronger single-query
signal and MO/GBIF has no directly equivalent flag exposed through this
query path.

Current coverage: 58 of 58 species have at least one photo (174 photos
total: 171 from iNaturalist, 3 from Mushroom Observer). The earlier gap --
Blewit (Clitocybe nuda) had no open-licensed research-grade photo on
iNaturalist under either its current name or the older synonym Lepista
nuda -- is now filled by Mushroom Observer, which is exactly the kind of
gap a second independent source exists to catch.

To refresh or extend photo coverage generally:

```
python3 scripts/fetch_photo_refs.py                  # full registry rebuild
python3 scripts/fetch_photo_refs.py "Amanita phalloides"   # single species test
```

## Known gaps / open items

- **This skill has no way to write to this repo, at all, by design.** The
  skill can always read this repo at runtime (see above), but everything
  it produces -- find logs included -- stays on local disk in the session
  it ran in. This repo used to also support pushing find-log entries
  straight to `main` through a small self-hosted relay service, built
  specifically to avoid the skill ever holding a GitHub credential
  directly (a session-level gate inside a Claude Cowork sandbox blocks
  both `api.github.com` and `git push` to `github.com` behind a
  repo-authorization step a fresh chat has no way to satisfy, so a direct
  push was never on the table regardless). That relay push feature was
  removed in September 2026, after being nice-to-have but not needed in
  practice, and after causing two separate rounds of a session correctly
  distrusting either the auto-push behavior or the live credential sitting
  in SKILL.md to make it work. Species corrections to
  `references/species_registry.json`, `lookalike_pairs.json`, or
  `toxin_syndromes.json` were never pushable this way even when the relay
  existed, and still aren't -- that stays a manual, human-reviewed
  `git add / commit / push`, same as always, given the life-safety stakes
  of that data.
- **Mushroom Observer's own site/API is unreachable from this project's
  usual build environment.** `fetch_photo_refs.py` works around this by
  querying MO's collection through GBIF instead (see that script's
  docstring, and the photo coverage section above) - functionally the same
  photos and licenses, just through a reachable mirror. If MO's API
  becomes directly reachable from wherever this is run, querying it
  directly instead would be a reasonable simplification, not a required
  one.
- **The photo-source and citation expansion (Sept 2026) was scoped, not
  exhaustive.** Species count went from 44 to 51 in the first pass and
  several thin citations got stronger sourcing, but this was a bounded
  pass -- more PNW species exist that aren't in here yet (this was never
  meant to be a complete regional flora -- see the scoping note in
  SKILL.md's "Notes for deployment" section), and MO/GBIF is currently
  used only as a photo-gap-filler, not queried as thoroughly as
  iNaturalist for every species. Both are fine places to keep extending
  from.
- **The edible-species deep-ID expansion (Sept 2026, second pass) was
  also scoped.** Went 51 -> 58 species (7 new Tier C edibles) and added
  spore print / cap+stem size / flesh-and-latex-behavior fields to all 38
  Tier C entries, but it's still not an exhaustive edible-species list for
  the region -- there are more common PNW edibles worth adding later (more
  Suillus species individually rather than grouped, more coral fungi
  species individually, additional milk caps, etc). The culinary side
  (taste, texture, prep, storage/preservation) was deliberately left out
  of this pass -- it was scoped to identification-relevant data, not a
  cookbook, so that's a reasonable next addition if wanted.

## Editing the database by hand

Each entry in `species_registry.json` follows one schema. Tier A/B entries
use `confused_with` / `cannot_determine_from_photo` / `distinguishing_test`;
Tier C entries use `watch_for` in their place, plus four deep-ID fields
that Tier A/B entries don't carry (those tiers already have
`distinguishing_test` doing that job):

```
id, common_name, scientific_name, tier, severity, toxin_syndrome, effect,
key_features, habitat_substrate, season_west_coast, range_notes,
confused_with, cannot_determine_from_photo, distinguishing_test,
citations, inat_taxon_name

# Tier C only, in place of confused_with/cannot_determine_from_photo/distinguishing_test:
watch_for, spore_print_color, cap_size, stem_size, flesh_characteristics
```

`spore_print_color`, `cap_size`, `stem_size`, and `flesh_characteristics`
are plain-language fields, not a fixed vocabulary -- write real ranges and
say when sources disagree rather than picking one number. For species with
no true stem (shelf fungi, puffballs, truffles) or no true cap (corals,
truffles), say "not applicable" or "no true stem" and note what's there
instead rather than leaving the field blank.

`inat_taxon_name` is what `fetch_photo_refs.py` queries against - it needs
to be a real, currently-accepted iNaturalist taxon name (species or genus
level), not a display label. If you rename or add a species, use its
current accepted scientific name here, not a common synonym, or the photo
fetch will silently come back empty.

Any change to `citations` should point at a real, checkable source -
this is life-safety reference data, not a place to guess.
