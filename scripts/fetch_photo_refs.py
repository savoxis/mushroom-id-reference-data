#!/usr/bin/env python3
"""
fetch_photo_refs.py - Builds references/photos_manifest.json by querying
two independent, citable photo sources per species: iNaturalist directly,
and Mushroom Observer's own collection by way of GBIF (see "Why GBIF and
not Mushroom Observer's own API" below).

Why iNaturalist and not a scrape of image search results: every observation
here is filtered to quality_grade=research, meaning the community has
converged on the species-level ID -- that is the correct rigor bar for a
life-safety reference database, a lot better than trusting whatever comes
back first from a generic image search. Each photo carries its own license
code and ready-made attribution string from the API directly, and this
script only ever stores the iNaturalist-hosted CDN URL plus that metadata --
it never downloads and rehosts the image itself, so there's no copyright
exposure from redistributing someone else's photo. Fetching the actual pixels
to look at one, when the skill needs to, happens at the point of use.

Why GBIF and not Mushroom Observer's own API: mushroomobserver.org itself
is not reachable from this project's usual build environment (a Claude
Cowork sandbox) -- verified live on 2026-09-16, every connection attempt to
mushroomobserver.org gets reset at the TLS handshake, on both the site and
its API, while every other host used in this project connects fine. That
looks like the host itself declining connections from this network range,
not a temporary blip. Mushroom Observer's own image collection is still
reachable indirectly, though: MO publishes its observation and image
records to GBIF (dataset key below), and api.gbif.org is reachable fine.
Querying GBIF for that one dataset's records is functionally the same
underlying photo collection, same licenses, same attribution, just fetched
through a mirror that happens to be reachable. If MO's own API becomes
reachable from wherever this script is actually run, querying it directly
would be a fine simplification -- this GBIF detour is a workaround for a
specific network's blind spot, not a rejection of MO's API on the merits.

Both sources are treated as first-class and merged into one photos list per
species, each photo tagged with a `source` field. iNaturalist is queried
first and MO/GBIF only tops up remaining slots up to PHOTOS_PER_SPECIES --
this isn't a ranking of one source's quality over the other, it's just
that iNaturalist's quality_grade=research filter gives a stronger
single-query confidence signal, so it goes first; MO/GBIF has no
directly equivalent flag exposed through this query path. Only photos with
an open license (cc0, cc-by, cc-by-sa, cc-by-nc, cc-by-nc-sa) are kept from
either source -- "all rights reserved" observations exist on both
platforms but this script skips them, even though linking to a page is a
different thing than republishing, because it costs nothing to stay on the
conservative side of the line for something meant to be reused.

CLI:
  python3 fetch_photo_refs.py                 -- run for every species in
                                                   references/species_registry.json
  python3 fetch_photo_refs.py "Taxon name"     -- run for one taxon, prints
                                                   the result instead of
                                                   writing the manifest

No API key required for either source. iNaturalist calls are rate-limited
to one request per second (a nonprofit-run API, not a resource to hammer);
GBIF is a large public infrastructure project built for this kind of
querying and is called without an artificial delay, still with the same
timeout/retry discipline as every other network call in this project.
"""

import sys
import json
import os
import re
import time
import urllib.request
import urllib.error
import urllib.parse

REFS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'references')
REGISTRY_PATH = os.path.join(REFS_DIR, 'species_registry.json')
MANIFEST_PATH = os.path.join(REFS_DIR, 'photos_manifest.json')

OPEN_LICENSES = {'cc0', 'cc-by', 'cc-by-sa', 'cc-by-nc', 'cc-by-nc-sa'}
PHOTOS_PER_SPECIES = 3
REQUEST_DELAY_SECONDS = 1.0

# GBIF dataset key for Mushroom Observer's published collection -- confirmed
# live via https://api.gbif.org/v1/dataset/search?q=Mushroom%20Observer on
# 2026-09-16. This is what lets fetch_from_mushroom_observer() reach MO's
# photos without ever connecting to mushroomobserver.org directly.
MO_GBIF_DATASET_KEY = 'd714382d-5890-4234-ae81-696eeb53658a'

# Maps a Creative Commons license URL (as GBIF's Audubon Core Multimedia
# extension returns it in the dc:rights / dc:license field) to the same
# short codes iNaturalist's API already uses, so both sources produce
# directly comparable 'license' values in the manifest.
CC_URL_TO_CODE = {
    'publicdomain/zero': 'cc0',
    'licenses/by/': 'cc-by',
    'licenses/by-sa/': 'cc-by-sa',
    'licenses/by-nc/': 'cc-by-nc',
    'licenses/by-nc-sa/': 'cc-by-nc-sa',
}


def _get(url, timeout=20, source_label='fetch'):
    req = urllib.request.Request(url, headers={'User-Agent': 'westcoast-mushroom-id (personal, non-commercial reference build)'})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            if attempt == 0:
                time.sleep(1)
            else:
                return {'error': f'{source_label} fetch failed: {e}'}


def fetch_from_inaturalist(taxon_name):
    """Returns a list of up to PHOTOS_PER_SPECIES photo reference dicts for
    one taxon name from iNaturalist, or an empty list (with a note) if
    nothing usable came back -- never raises, this is a best-effort
    enrichment step, not a thing that should be able to break the build."""
    url = (
        "https://api.inaturalist.org/v1/observations"
        f"?taxon_name={urllib.parse.quote(taxon_name)}"
        "&quality_grade=research&photos=true&per_page=30"
        "&order=desc&order_by=votes"
    )
    data = _get(url, source_label='iNaturalist')
    if not data or 'error' in data:
        return [], (data or {}).get('error', 'no response')
    if not data.get('results'):
        return [], 'no research-grade observations with photos found'

    is_genus_query = ' ' not in taxon_name.strip()

    out = []
    for obs in data['results']:
        taxon_name_actual = obs.get('taxon', {}).get('name', '')
        if not taxon_name_actual:
            continue
        if is_genus_query:
            # A bare genus name (e.g. "Morchella") is deliberate for
            # genus-level entries -- accept any species within that genus
            # rather than requiring an exact match nothing will ever hit.
            if not taxon_name_actual.lower().startswith(taxon_name.lower() + ' ') and \
               taxon_name_actual.lower() != taxon_name.lower():
                continue
        else:
            # Species-level query: guard against the API's fuzzy name
            # matching drifting to a different species than asked for.
            if taxon_name_actual.lower() != taxon_name.lower():
                continue
        # Take at most one photo per observation -- three near-duplicate
        # shots of the same specimen from the same angle are a lot less
        # useful as a reference set than one photo each from three
        # different finds/photographers.
        for photo in obs.get('photos', []):
            license_code = photo.get('license_code')
            if license_code not in OPEN_LICENSES:
                continue
            raw_url = photo.get('url', '')
            # Swap the thumbnail-size path segment for the medium-size one.
            # iNaturalist's extension casing on this URL varies per photo
            # (.jpg/.JPG/.jpeg/.JPEG) -- match case-insensitively so a photo
            # with an uppercase extension doesn't silently fall through and
            # keep pointing at the tiny square thumbnail (found live while
            # testing this script on 2026-09-16).
            medium_url = re.sub(r'square(\.jpe?g)', r'medium\1', raw_url, flags=re.IGNORECASE)
            out.append({
                'url': medium_url,
                'license': license_code,
                'attribution': photo.get('attribution', ''),
                'observation_url': obs.get('uri', ''),
                'observer': obs.get('user', {}).get('name') or obs.get('user', {}).get('login', ''),
                'observed_on': obs.get('observed_on'),
                'source': 'inaturalist',
            })
            break
        if len(out) >= PHOTOS_PER_SPECIES:
            break
    if not out:
        return [], 'observations found but none had an open-licensed photo'
    return out, None


def _cc_license_code(license_url_or_text):
    """Normalizes a Creative Commons rights value (GBIF returns these as
    full license URLs, e.g. https://creativecommons.org/licenses/by-nc-sa/4.0/)
    to the same short codes iNaturalist's API already uses. Returns None for
    anything not recognized as an open CC license -- callers treat that the
    same as 'all rights reserved' and skip the photo."""
    if not license_url_or_text:
        return None
    text = license_url_or_text.lower()
    for fragment, code in CC_URL_TO_CODE.items():
        if fragment in text:
            return code
    return None


def fetch_from_mushroom_observer(taxon_name, limit):
    """Returns up to `limit` photo reference dicts for one taxon name from
    Mushroom Observer's collection, reached via GBIF (see the module
    docstring for why GBIF rather than MO's own API). Same never-raises,
    best-effort contract as fetch_from_inaturalist. `limit` lets the caller
    ask for only as many photos as are still needed to reach
    PHOTOS_PER_SPECIES after iNaturalist's results."""
    if limit <= 0:
        return [], None
    url = (
        "https://api.gbif.org/v1/occurrence/search"
        f"?scientificName={urllib.parse.quote(taxon_name)}"
        f"&datasetKey={MO_GBIF_DATASET_KEY}"
        "&mediaType=StillImage&limit=30"
    )
    data = _get(url, source_label='GBIF/Mushroom Observer')
    if not data or 'error' in data:
        return [], (data or {}).get('error', 'no response')
    results = data.get('results') or []
    if not results:
        return [], 'no Mushroom Observer records with photos found (via GBIF)'

    out = []
    for occ in results:
        media_list = (occ.get('extensions', {}) or {}).get('http://rs.tdwg.org/ac/terms/Multimedia', [])
        for media in media_list:
            license_code = _cc_license_code(
                media.get('http://purl.org/dc/terms/rights')
                or media.get('http://ns.adobe.com/xap/1.0/rights/UsageTerms')
            )
            if license_code not in OPEN_LICENSES:
                continue
            image_url = (media.get('http://rs.tdwg.org/ac/terms/goodQualityAccessURI')
                         or media.get('http://rs.tdwg.org/ac/terms/accessURI')
                         or media.get('http://purl.org/dc/terms/identifier'))
            if not image_url:
                continue
            out.append({
                'url': image_url,
                'license': license_code,
                'attribution': occ.get('recordedBy', '') or 'Mushroom Observer contributor',
                'observation_url': occ.get('occurrenceID', ''),
                'observer': occ.get('recordedBy', ''),
                'observed_on': occ.get('eventDate', '')[:10] if occ.get('eventDate') else None,
                'source': 'mushroom_observer_via_gbif',
            })
            break  # at most one photo per occurrence, same reasoning as iNaturalist above
        if len(out) >= limit:
            break
    if not out:
        return [], 'Mushroom Observer records found (via GBIF) but none had an open-licensed photo'
    return out, None


def fetch_for_taxon(taxon_name):
    """Combined fetch: iNaturalist first, then Mushroom Observer (via GBIF)
    tops up any remaining slots up to PHOTOS_PER_SPECIES. Returns
    (photos, note) where note is only set when BOTH sources came back
    empty -- a partial result from one source is not a failure."""
    inat_photos, inat_note = fetch_from_inaturalist(taxon_name)
    remaining = PHOTOS_PER_SPECIES - len(inat_photos)
    mo_photos, mo_note = fetch_from_mushroom_observer(taxon_name, remaining)
    photos = inat_photos + mo_photos
    if not photos:
        combined_note = f"iNaturalist: {inat_note or 'no photos'}; Mushroom Observer/GBIF: {mo_note or 'no photos'}"
        return [], combined_note
    return photos, None


def main():
    if len(sys.argv) > 1:
        taxon_name = sys.argv[1]
        photos, note = fetch_for_taxon(taxon_name)
        print(json.dumps({'taxon_name': taxon_name, 'photos': photos, 'note': note}, indent=2))
        return

    if not os.path.exists(REGISTRY_PATH):
        print(json.dumps({'error': f'species registry not found at {REGISTRY_PATH}'}))
        sys.exit(1)

    with open(REGISTRY_PATH) as f:
        species = json.load(f)

    manifest = {}
    warnings = []
    for i, sp in enumerate(species):
        taxon_name = sp.get('inat_taxon_name') or sp['scientific_name']
        photos, note = fetch_for_taxon(taxon_name)
        manifest[sp['id']] = {
            'common_name': sp['common_name'],
            'scientific_name': sp['scientific_name'],
            'tier': sp['tier'],
            'photos': photos,
        }
        status = f"{len(photos)} photo(s)" if photos else f"NONE -- {note}"
        print(f"[{i+1}/{len(species)}] {sp['common_name']} ({taxon_name}): {status}")
        if not photos:
            warnings.append(f"{sp['common_name']} ({taxon_name}): {note}")
        if i < len(species) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    with open(MANIFEST_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f"\nWrote {MANIFEST_PATH}")
    if warnings:
        print(f"\n{len(warnings)} species came back with no usable photo -- needs a manual look:")
        for w in warnings:
            print(f"  - {w}")


if __name__ == '__main__':
    main()
