# mushroom-id-reference-data

Companion data repo for the `westcoast-mushroom-id` Claude skill. The skill
itself (SKILL.md) lives in Claude's skill system, not here. This repo holds
the one thing that genuinely benefits from living outside SKILL.md: the
species database and its sourced reference photos, plus a couple of
maintenance-only tools that a human runs by hand to build that data. It
does not hold anything the skill executes at runtime -- see "Why this
split exists" for why that changed, and it changed twice.

Renamed from `mushroom-identifier-dontusethisever` (September 2026). That
name was a joke when the repo was young; in practice it read as a warning
label on the repo's own clone URL, and a fresh Claude session correctly
balked at cloning something whose name told it not to, no matter what
SKILL.md said around it. A repo name is not a place for a joke that every
future session has to read cold.

## Why this split exists

Claude's `propose_skills` tool only accepts a single SKILL.md file - no
bundled folders, no separate script files. The first version of this skill
crammed the entire species database (44 species, citations, lookalike
tables) and three Python scripts into one ~1700-line markdown file. It
worked, but it was a bad way to maintain a life-safety reference: a single
bad find-and-replace could silently corrupt a species entry, there was no
way to diff a change to just the toxin table, and updating one photo
reference meant re-proposing the whole skill. So everything -- data and
scripts alike -- moved out to this repo, fetched at runtime over
`raw.githubusercontent.com` or a fresh clone.

That fixed the data problem but created a different one: every session now
had to clone a repo and run `.py` files pulled off the internet before
looking at them, sight unseen, on nothing but this file's word that they
were fine. Two separate Claude sessions correctly treated that as
something to be suspicious of, once over a live credential sitting in
SKILL.md (see the find-log history below) and again, after that was fixed,
over the shape of the thing itself -- fetching and executing code it had
never seen. Reassuring language in SKILL.md never fixed that, because it
can't: a document arguing "trust me, this is fine" is exactly what an
untrustworthy document would also say, so no amount of it changes whether
a wary reader should believe it.

The actual fix (September 2026, second pass): the five scripts the skill
runs at runtime -- `geo.py`, `exif_extract.py`, `weather_at_time.py`,
`fetch_reference_data.py`, `log_find.py` -- moved into SKILL.md itself,
written out verbatim in its Setup section. A session reads them as part of
reading the skill, the same way it reads everything else in that file, and
never fetches-and-executes anything sight unseen. Only the species
database stays here, as plain JSON with no executable content in it at
all -- there's nothing in a `.json` fetch for a wary session to have to
trust, and it keeps the actual benefit this split was for in the first
place: editing a species entry or fixing a bad photo reference is still a
normal git commit to a data file, not a skill re-proposal. The tradeoff is
that a change to one of the five scripts now needs a skill re-propose
instead of a git commit -- worth it, since those change rarely and the
database doesn't.

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
  fetch_photo_refs.py      the tool that built photos_manifest.json against
                            two independent sources -- iNaturalist directly,
                            and Mushroom Observer's collection by way of
                            GBIF (see the script's own docstring for why
                            GBIF and not Mushroom Observer's API directly)
                            - rerun this to refresh or extend photo
                            coverage, not by hand-editing the manifest.
                            Maintenance-only: a human runs this by hand,
                            the skill itself never fetches or executes it.
  test_exif_extract.py     unit tests for exif_extract.py's DMS/EXIF parsing
                            logic. Dev-only, not part of the skill's runtime
                            path either.
```

The five scripts the skill actually runs at runtime --
`exif_extract.py` (EXIF GPS/timestamp), `geo.py` (geocoding and
in-scope check), `weather_at_time.py` (historical conditions),
`fetch_reference_data.py` (pulls the four `references/*.json` files
below), and `log_find.py` (appends to a local find log, no network call,
no credential) -- live embedded in SKILL.md's Setup section now, not
here. See "Why this split exists" above for why, and `logs/` (the local
find history) is generated wherever the skill runs, local disk only --
it was never part of this repo's tracked content and isn't shipped here.

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

SKILL.md has a small bootstrap script embedded directly in it that pulls
the four `references/*.json` files down at the start of a run -- nothing
else. It tries `git clone --depth 1` against this repo first, and if that
fails, falls back to pulling each JSON file individually from
`raw.githubusercontent.com`. Either path only ever writes `.json`, so
there's no executable content anywhere in what a session fetches from
GitHub at runtime.

That fallback matters: this Cowork sandbox currently has `api.github.com`
blocked at the session level (see "Known gaps" below), but
`raw.githubusercontent.com` - a plain static-content CDN, different domain,
no auth needed for a public repo - is not affected. `fetch_reference_data.py`
(embedded in SKILL.md, see "Why this split exists" above) uses the same
raw-CDN approach with a 24-hour cache, and fails safe: if a fetch fails or
comes back invalid, it keeps using whatever copy is already on disk
instead of deleting good data over a network hiccup.

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
