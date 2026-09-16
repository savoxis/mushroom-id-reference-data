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
explicit choice, so find-log push doesn't need a separate per-device setup
step. That's a real tradeoff against keeping it off a file that syncs
everywhere the skill loads -- accepted deliberately, because the token
itself is scoped narrow enough (append-only, schema-validated, one file)
that the worst case of it leaking is spam in a log, not a repo compromise.
Rotate it by regenerating a new token, updating the relay container's
`.env`, and updating the value in SKILL.md (then re-saving the skill).

**"Embedded" is not the same as "auto-used."** The credentials being
present in SKILL.md doesn't mean a session is supposed to export them and
push without asking -- SKILL.md's workflow (step 12) asks the user, once
per session, before the first push, whether they want finds pushed to
GitHub or kept local, and only proceeds on a clear yes. This surfaced as a
real issue in practice: a session correctly declined to auto-run the
bootstrap credential export and silent push, since a skill file embedding
a credential can't pre-authorize sending someone's data externally on
their behalf. That's the system working as intended, not a bug -- the fix
was adding the explicit per-session ask to the skill itself (September
2026), not trying to make sessions more willing to auto-push.

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

- **Consent-gated push for find logs is live (no longer "auto").** The
  skill can always read this repo at runtime (see above), but it can't
  commit or push to it directly - a session-level gate inside a Claude
  Cowork sandbox blocks both `api.github.com` and `git push` to
  `github.com` behind a repo-authorization step that a fresh chat has no
  way to satisfy (confirmed this is not a credentials problem - a valid
  PAT supplied directly gets denied identically to no PAT at all, because
  the block happens before any credential is even checked). That's a
  structural property of the sandbox, not something fixable from inside a
  session, and it won't get better by waiting - a skill invoked in a new
  chat never has a "workspace" carried over from a previous one anyway.
  So instead of fighting that gate, `log_find.py` CAN push find-log
  entries through `relay/` - a small self-hosted service (see
  `relay/README.md`) that lives off-sandbox and is the only thing that
  ever actually holds a GitHub credential. It's deliberately narrow: one
  endpoint, one file it's allowed to touch (`logs/finds_log.jsonl`),
  strict schema validation before anything reaches GitHub. The relay's
  own auth token is embedded in SKILL.md so no per-device setup is needed
  (see the tradeoff note above), but the push itself only happens after
  the skill asks the user, once per session, and gets a yes - see
  SKILL.md's workflow step 12. This was originally framed as fully
  automatic ("works every session with no confirmation"), and that framing
  was wrong: a session correctly declined to auto-export the token and
  push location data without asking, which is what surfaced the need for
  this gate (fixed September 2026). The actual GitHub PAT never leaves
  the relay container regardless. If the relay is declined, unconfigured,
  or unreachable, `log_find.py` degrades to exactly what it did before
  the relay existed - local write only, no error, nothing missing.
- **Species corrections are NOT pushable this way, on purpose.** Editing
  `references/species_registry.json`, `lookalike_pairs.json`, or
  `toxin_syndromes.json` stays a manual, human-reviewed
  `git add / commit / push`, same as always. Given the life-safety stakes
  of that data, "the skill can silently rewrite the deadly-species
  database on its own" is not a capability this project wants, even with
  a relay in place - the relay's whole design intentionally has no route
  for it.
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
