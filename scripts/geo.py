#!/usr/bin/env python3
"""
geo.py - Geocoding, elevation, and in-scope check for the West Coast
mushroom-id skill. Adapted from oregon-mushroom-scout's geo.py, widened
from a single-state box to OR/WA/CA/north ID/west MT.

CLI: python3 geo.py "location_string"
Returns JSON: {lat, lon, elev_ft, matched, in_scope, ambiguous: [...], error}

Scope check is two-layer, not a precise polygon:
1. Preferred: Open-Meteo's geocoder returns an admin1 (state) name -- match
   against the five target states directly.
2. Idaho and Montana are only partly in scope ("north Idaho", "west
   Montana"), so admin1== 'Idaho' additionally requires lat >= 46.0
   (roughly the panhandle) and admin1== 'Montana' additionally requires
   lon <= -112.5 (roughly west of the Continental Divide corridor).
3. Fallback for raw "lat,lon" input with no admin1 available: a generous
   rectangular bounding box. This over-includes parts of Nevada, southern
   Idaho, and similar edge areas -- it's a coarse sanity check, not a
   state-boundary lookup. Treat "in_scope: true" from the box path as
   provisional and say so if it matters.
"""

import sys
import json
import math
import urllib.request
import urllib.error
import urllib.parse
import os
import time

CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
GEOCODE_CACHE = os.path.join(CACHE_DIR, 'geocode_cache.json')
ELEV_CACHE = os.path.join(CACHE_DIR, 'elevation_cache.json')

# Coarse fallback box only -- see module docstring. Covers OR/WA/CA fully,
# overshoots east to catch north ID / west MT, accepting some false
# positives (NV, southern ID, etc.) as the cost of a simple rectangle.
BBOX = {"min_lat": 32.5, "max_lat": 49.5, "min_lon": -124.8, "max_lon": -111.0}
TARGET_STATES = {"Oregon", "Washington", "California"}


def ensure_cache_dir():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)


def read_cache(cache_file, ttl_seconds):
    if not os.path.exists(cache_file):
        return {}
    try:
        with open(cache_file) as f:
            cache = json.load(f)
        now = time.time()
        return {k: v['data'] for k, v in cache.items() if now - v.get('timestamp', 0) < ttl_seconds}
    except Exception:
        return {}


def write_cache(cache_file, key, data):
    ensure_cache_dir()
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    cache[key] = {'data': data, 'timestamp': time.time()}
    try:
        with open(cache_file, 'w') as f:
            json.dump(cache, f)
    except Exception:
        pass


def in_scope_by_admin1(admin1, lat):
    if admin1 in TARGET_STATES:
        return True
    if admin1 == "Idaho" and lat is not None and lat >= 46.0:
        return True
    if admin1 == "Montana":
        return None  # longitude check happens where lon is available (caller)
    return False


def in_scope_by_box(lat, lon):
    return BBOX['min_lat'] <= lat <= BBOX['max_lat'] and BBOX['min_lon'] <= lon <= BBOX['max_lon']


def parse_raw_coords(location):
    parts = location.split(',')
    if len(parts) != 2:
        return None
    try:
        lat = float(parts[0].strip())
        lon = float(parts[1].strip())
        return {'lat': lat, 'lon': lon}
    except (ValueError, AttributeError):
        return None


def geocode_open_meteo(location):
    """
    Geocode a place name. IMPORTANT: query Open-Meteo with the bare place
    name only ("Bend", not "Bend, Oregon") -- appending a state to the
    search string degrades the match (tested live: "Boise, Idaho" as a
    literal search string returned an obscure northern-Idaho peak instead
    of the actual city). Split "City, State" input ourselves and use the
    state half only as a preference hint, not as part of the search text.

    Disambiguation: Open-Meteo returns multiple same-named places with no
    inherent relevance ranking, so results are ranked by population (a
    real city beats a same-named glacier, dam, or hamlet) and by whether
    the feature_code marks a populated place (starts with "P"). Verified
    against live data: a bare "Boise" query correctly returns Boise, Idaho
    (pop 235684) first, and a bare "Bend" query correctly returns Bend,
    Oregon (pop 87014) first.
    """
    cache = read_cache(GEOCODE_CACHE, 86400 * 30)
    if location in cache:
        return cache[location]

    if ',' in location:
        search_term, _, state_hint = location.partition(',')
        search_term, state_hint = search_term.strip(), state_hint.strip()
    else:
        search_term, state_hint = location.strip(), None

    url = (f"https://geocoding-api.open-meteo.com/v1/search"
           f"?name={urllib.parse.quote(search_term)}&count=10&language=en&format=json&countryCode=US")

    data = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'westcoast-mushroom-id'})
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode())
                break
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            if attempt == 0:
                time.sleep(1)
            else:
                return {'error': f'geocoding unavailable: {str(e)}'}

    if not data or 'results' not in data or not data['results']:
        return {'error': 'location not found'}

    results = data['results']

    # A state hint narrows the pool but never discards it entirely --
    # the hint could be wrong, abbreviated, or just absent from this result set.
    if state_hint:
        hinted = [r for r in results if r.get('admin1', '').lower() == state_hint.lower()]
        if hinted:
            results = hinted

    def rank_key(r):
        is_populated_place = 1 if str(r.get('feature_code', '')).startswith('P') else 0
        return (is_populated_place, r.get('population') or 0)

    results = sorted(results, key=rank_key, reverse=True)

    matches = []
    for r in results:
        admin1 = r.get('admin1', '')
        lat, lon = r['latitude'], r['longitude']
        scope = in_scope_by_admin1(admin1, lat)
        if scope is None:  # Montana case, needs lon
            scope = lon <= -112.5
        matches.append({'name': r.get('name', ''), 'lat': lat, 'lon': lon, 'admin1': admin1,
                         'in_scope': scope, 'population': r.get('population'),
                         'feature_code': r.get('feature_code')})

    top = matches[0]
    runner_up_pop = (matches[1]['population'] or 0) if len(matches) > 1 else 0
    confident = (len(matches) == 1) or (
        str(top['feature_code'] or '').startswith('P') and (top['population'] or 0) > 0 and
        (top['population'] or 0) >= 3 * runner_up_pop + 1
    )

    if confident:
        result = {'lat': top['lat'], 'lon': top['lon'], 'matched': f"{top['name']}, {top['admin1']}",
                   'in_scope': top['in_scope'], 'ambiguous': []}
        write_cache(GEOCODE_CACHE, location, result)
        return result
    else:
        result = {'matched': None,
                   'ambiguous': [f"{m['name']}, {m['admin1']} (pop {m['population'] or 'unknown'})" for m in matches[:6]]}
        write_cache(GEOCODE_CACHE, location, result)
        return result


def get_elevation(lat, lon):
    cache = read_cache(ELEV_CACHE, 86400 * 365)
    key = f"{lat:.4f},{lon:.4f}"
    if key in cache:
        return cache[key]

    url = f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}"
    data = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'westcoast-mushroom-id'})
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode())
                break
        except (urllib.error.URLError, urllib.error.HTTPError, Exception):
            if attempt == 0:
                time.sleep(1)
            else:
                return None

    if not data:
        return None
    elev_m = data.get('elevation', [None])[0]
    if elev_m is None:
        return None
    elev_ft = int(round(elev_m * 3.28084))
    write_cache(ELEV_CACHE, key, elev_ft)
    return elev_ft


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2_rad - lat1_rad, lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: geo.py location_string'}))
        sys.exit(1)

    location = sys.argv[1]

    coords = parse_raw_coords(location)
    if coords:
        elev = get_elevation(coords['lat'], coords['lon'])
        result = {'lat': coords['lat'], 'lon': coords['lon'], 'elev_ft': elev,
                  'matched': 'raw_coordinates',
                  'in_scope': in_scope_by_box(coords['lat'], coords['lon']),
                  'in_scope_note': 'raw coordinates only checked against a coarse bounding box, not state boundaries',
                  'ambiguous': []}
        print(json.dumps(result))
        sys.exit(0)

    result = geocode_open_meteo(location)
    if result.get('error') or result.get('matched') is None:
        print(json.dumps(result))
        sys.exit(0)

    elev = get_elevation(result['lat'], result['lon'])
    if elev:
        result['elev_ft'] = elev
    print(json.dumps(result))
    sys.exit(0)


if __name__ == '__main__':
    main()
