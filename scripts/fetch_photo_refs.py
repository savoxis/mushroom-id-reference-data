#!/usr/bin/env python3
"""
fetch_photo_refs.py - Builds references/photos_manifest.json by querying
iNaturalist's public API for real, citable reference photos per species.

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

Only photos with an open license (cc0, cc-by, cc-by-sa, cc-by-nc,
cc-by-nc-sa) are kept -- "all rights reserved" observations exist on
iNaturalist but this script skips them, even though linking to a page is a
different thing than republishing, because it costs nothing to stay on the
conservative side of the line for something meant to be reused.

CLI:
  python3 fetch_photo_refs.py                 -- run for every species in
                                                   references/species_registry.json
  python3 fetch_photo_refs.py "Taxon name"     -- run for one taxon, prints
                                                   the result instead of
                                                   writing the manifest

No API key required. Rate-limited to one request per second -- iNaturalist
is a nonprofit-run API, not a resource to hammer.
"""

import sys
import json
import os
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


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': 'westcoast-mushroom-id (personal, non-commercial reference build)'})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            if attempt == 0:
                time.sleep(1)
            else:
                return {'error': f'iNaturalist fetch failed: {e}'}


def fetch_for_taxon(taxon_name):
    """Returns a list of up to PHOTOS_PER_SPECIES photo reference dicts for
    one taxon name, or an empty list (with a note) if nothing usable came
    back -- never raises, this is a best-effort enrichment step, not a
    thing that should be able to break the build."""
    url = (
        "https://api.inaturalist.org/v1/observations"
        f"?taxon_name={urllib.parse.quote(taxon_name)}"
        "&quality_grade=research&photos=true&per_page=30"
        "&order=desc&order_by=votes"
    )
    data = _get(url)
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
            medium_url = photo.get('url', '').replace('square.jpe', 'medium.jpe').replace('square.jpg', 'medium.jpg')
            out.append({
                'url': medium_url,
                'license': license_code,
                'attribution': photo.get('attribution', ''),
                'observation_url': obs.get('uri', ''),
                'observer': obs.get('user', {}).get('name') or obs.get('user', {}).get('login', ''),
                'observed_on': obs.get('observed_on'),
            })
            break
        if len(out) >= PHOTOS_PER_SPECIES:
            break
    if not out:
        return [], 'observations found but none had an open-licensed photo'
    return out, None


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
