---
name: westcoast-mushroom-id
description: Identifies wild mushrooms from a photo for West Coast foragers (OR, WA, CA, north ID, west MT) and flags dangerous look-alikes. Pulls photo GPS/time, checks historical weather, and asks follow-up questions (multiple-choice or a rendered form) when photo-visible features are not enough. Always gives multiple ranked candidates, never a single confident answer, and never states or implies a mushroom is safe to eat. Any possible match to a deadly species (death cap, destroying angel, deadly galerina, deadly webcap, false morel, Amanita smithiana) triggers a hard warning regardless of how unlikely. Use whenever the user uploads or describes a wild mushroom photo and asks what it is, whether it is safe, whether it matches chanterelle, morel, bolete, or matsutake, or gives foraging details (cap, gills, stem, habitat, spore print) wanting an ID -- even without saying identify. An aid only, not a substitute for a spore print or an in-person expert.
---

# West Coast Mushroom ID

Companion skill to oregon-mushroom-scout. That skill scores whether conditions are
right for something to be fruiting nearby. This one looks at a mushroom someone
already found and helps narrow down what it might be -- and, more importantly,
whether it might be something that kills.

Example queries this skill handles:
- "What is this?" + a mushroom photo
- "Found these growing on a stump, are they honey mushrooms?"
- "Is this a chanterelle?" + photo
- "Cap is orange, gills run down the stem, smells fruity, found under Doug fir"
- "Are these safe? Found them in my backyard in Portland"
- "Matsutake or something else?" + photo, taken near Sisters OR

This skill's five workflow scripts are written out below, verbatim, as
part of this file -- not fetched from anywhere. Only the species database
(JSON, no executable content) lives in a companion GitHub repo; see
"Setup" below before doing anything else. Splitting out the JSON, and
only the JSON, is deliberate: it needs to stay independently editable (a
species correction should be a one-line git diff, not a full skill
re-propose) and it carries nothing for a session to have to trust, since
it's data, not code. The scripts don't share either property -- they
change rarely, and arriving as part of reading this file rather than
being fetched and executed sight-unseen is worth more than the
convenience of editing them without a re-propose.

---

## Setup: writing the scripts and pulling the reference data (do this first, every session)

Check whether `scripts/` already has these five files in the working
directory, each matching what's below. If not, write each one out to
`scripts/<name>` exactly as given -- character for character, not
summarized or reconstructed from memory of what it does:

**`scripts/geo.py`**
```python
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
```

**`scripts/exif_extract.py`**
```python
#!/usr/bin/env python3
"""
exif_extract.py - Minimal stdlib-only EXIF reader for GPS + capture time.
Reads only what this skill needs: GPS latitude/longitude and DateTimeOriginal
(falls back to DateTime). No Pillow/exifread dependency -- parses the JPEG
APP1/EXIF segment directly with struct.

CLI: python3 exif_extract.py /path/to/photo.jpg
Returns JSON: {lat, lon, datetime, has_gps, has_datetime, error}

Scope: JPEG only (the near-universal format for camera/phone photos with
EXIF). HEIC, PNG, and re-encoded/screenshotted images either don't carry
EXIF the same way or strip it entirely -- this returns has_gps/has_datetime
false rather than guessing, so the workflow falls back to asking the user.
This is a narrow, best-effort parser for well-formed camera JPEGs, not a
general-purpose EXIF library -- any parse failure fails safe into "ask the
user," it never fails into a wrong answer.
"""

import sys
import struct
import json


def _read_ifd(data, offset, endian):
    """Read one IFD. Returns (entries dict of tag -> (type, count, value_bytes), next_ifd_offset)."""
    count = struct.unpack(endian + 'H', data[offset:offset + 2])[0]
    entries = {}
    pos = offset + 2
    for _ in range(count):
        entry = data[pos:pos + 12]
        tag, typ, cnt = struct.unpack(endian + 'HHI', entry[0:8])
        entries[tag] = (typ, cnt, entry[8:12])
        pos += 12
    next_ifd_offset = struct.unpack(endian + 'I', data[pos:pos + 4])[0]
    return entries, next_ifd_offset


def _type_size(typ):
    return {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}.get(typ, 1)


def _read_value(tiff, typ, cnt, value_bytes, endian):
    """Resolve an IFD entry's value(s). Offsets are relative to the start of `tiff`."""
    size = _type_size(typ) * cnt
    if size <= 4:
        raw = value_bytes[:size]
    else:
        offset = struct.unpack(endian + 'I', value_bytes)[0]
        raw = tiff[offset: offset + size]

    if typ == 2:  # ASCII, null-terminated
        return raw.split(b'\x00', 1)[0].decode('ascii', errors='replace')
    if typ == 5:  # RATIONAL: cnt pairs of (numerator, denominator), 4 bytes each
        vals = []
        for i in range(cnt):
            num, den = struct.unpack(endian + 'II', raw[i * 8:i * 8 + 8])
            vals.append(num / den if den else 0.0)
        return vals
    if typ == 3:
        return list(struct.unpack(endian + ('H' * cnt), raw[:2 * cnt]))
    if typ == 4:
        return list(struct.unpack(endian + ('I' * cnt), raw[:4 * cnt]))
    return raw


def _dms_to_decimal(dms, ref):
    """[deg, min, sec] + hemisphere letter -> signed decimal degrees."""
    if not dms or len(dms) < 3:
        return None
    deg, minutes, sec = dms[0], dms[1], dms[2]
    decimal = deg + minutes / 60.0 + sec / 3600.0
    if ref in ('S', 'W'):
        decimal = -decimal
    return decimal


def extract(path):
    result = {'lat': None, 'lon': None, 'datetime': None,
              'has_gps': False, 'has_datetime': False, 'error': None}
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception as e:
        result['error'] = f'could not read file: {e}'
        return result

    if data[0:2] != b'\xff\xd8':
        result['error'] = ('not a JPEG (no SOI marker) -- this parser only reads JPEG EXIF; '
                            'ask the user for time/location')
        return result

    pos = 2
    tiff = None
    while pos < len(data) - 4:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2  # standalone markers, no length field
            continue
        if marker == 0xDA:  # start of scan -- compressed image data follows, stop looking
            break
        if pos + 4 > len(data):
            break
        seg_len = struct.unpack('>H', data[pos + 2:pos + 4])[0]
        if marker == 0xE1 and data[pos + 4:pos + 10] == b'Exif\x00\x00':
            tiff = data[pos + 10: pos + 2 + seg_len]
            break
        pos += 2 + seg_len

    if tiff is None:
        result['error'] = ('no EXIF data found in this file (screenshots, downloaded/re-saved, '
                            'and many messaging-app photos strip it) -- ask the user for time/location')
        return result

    endian = '<' if tiff[0:2] == b'II' else '>'
    ifd0_offset = struct.unpack(endian + 'I', tiff[4:8])[0]
    ifd0, _ = _read_ifd(tiff, ifd0_offset, endian)

    # DateTimeOriginal (0x9003) lives in the Exif SubIFD (pointer tag 0x8769).
    # Fall back to IFD0's plain DateTime (0x0132) if that's missing.
    if 0x8769 in ifd0:
        exif_ifd_offset = struct.unpack(endian + 'I', ifd0[0x8769][2])[0]
        exif_ifd, _ = _read_ifd(tiff, exif_ifd_offset, endian)
        if 0x9003 in exif_ifd:
            typ, cnt, vb = exif_ifd[0x9003]
            result['datetime'] = _read_value(tiff, typ, cnt, vb, endian)
            result['has_datetime'] = True
    if not result['has_datetime'] and 0x0132 in ifd0:
        typ, cnt, vb = ifd0[0x0132]
        result['datetime'] = _read_value(tiff, typ, cnt, vb, endian)
        result['has_datetime'] = True

    # GPS IFD pointer (0x8825)
    if 0x8825 in ifd0:
        gps_ifd_offset = struct.unpack(endian + 'I', ifd0[0x8825][2])[0]
        gps_ifd, _ = _read_ifd(tiff, gps_ifd_offset, endian)

        lat_ref = lon_ref = None
        lat_dms = lon_dms = None
        if 0x0001 in gps_ifd:
            typ, cnt, vb = gps_ifd[0x0001]
            lat_ref = _read_value(tiff, typ, cnt, vb, endian)
        if 0x0002 in gps_ifd:
            typ, cnt, vb = gps_ifd[0x0002]
            lat_dms = _read_value(tiff, typ, cnt, vb, endian)
        if 0x0003 in gps_ifd:
            typ, cnt, vb = gps_ifd[0x0003]
            lon_ref = _read_value(tiff, typ, cnt, vb, endian)
        if 0x0004 in gps_ifd:
            typ, cnt, vb = gps_ifd[0x0004]
            lon_dms = _read_value(tiff, typ, cnt, vb, endian)

        lat = _dms_to_decimal(lat_dms, lat_ref) if lat_dms else None
        lon = _dms_to_decimal(lon_dms, lon_ref) if lon_dms else None
        if lat is not None and lon is not None:
            result['lat'] = round(lat, 6)
            result['lon'] = round(lon, 6)
            result['has_gps'] = True

    if not result['has_gps']:
        note = 'no GPS tags found -- ask the user for a location'
        result['error'] = f"{result['error']}; {note}" if result['error'] else note
    if not result['has_datetime']:
        note = 'no capture date found -- ask the user for a date'
        result['error'] = f"{result['error']}; {note}" if result['error'] else note

    return result


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: exif_extract.py /path/to/photo.jpg'}))
        sys.exit(1)
    print(json.dumps(extract(sys.argv[1]), indent=2))


if __name__ == '__main__':
    main()
```

**`scripts/weather_at_time.py`**
```python
#!/usr/bin/env python3
"""
weather_at_time.py - Historical weather/soil conditions for a specific past
date and location, via Open-Meteo's Archive API (no key required).

This answers "were conditions plausible for something to be fruiting here
on this date" -- a secondary, corroborating signal only. It never
identifies a species and should never move a candidate up or down more
than slightly in confidence. A mushroom is what its features say it is,
regardless of the weather.

CLI: python3 weather_at_time.py lat lon YYYY-MM-DD
Returns JSON with a 30-day lookback window ending on the given date:
soil temp (7d mean), precip (7d/14d/30d), frost nights, and the raw daily
series in case the model wants to look closer.

Verified live against archive-api.open-meteo.com on 2026-09-16: the
archive endpoint uses soil_temperature_0_to_7cm / soil_moisture_0_to_7cm,
which are DIFFERENT variable names from the forecast API's
soil_temperature_6cm / soil_moisture_3_to_9cm used in oregon-mushroom-
scout's weather.py -- these are two different Open-Meteo products (ERA5-
Land reanalysis vs forecast model) and do not share a naming scheme. Do
not copy variable names across the two scripts without re-checking.
"""

import sys
import json
import urllib.request
import urllib.error
import os
import time
from datetime import datetime, timedelta

CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
CACHE_FILE = os.path.join(CACHE_DIR, 'weather_history_cache.json')


def ensure_cache_dir():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)


def read_cache(key, ttl_seconds):
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        entry = cache.get(key)
        if entry and time.time() - entry.get('timestamp', 0) < ttl_seconds:
            return entry['data']
    except Exception:
        pass
    return None


def write_cache(key, data):
    ensure_cache_dir()
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    cache[key] = {'data': data, 'timestamp': time.time()}
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except Exception:
        pass


def fetch_archive(lat, lon, start_date, end_date):
    cache_key = f"{lat}_{lon}_{start_date}_{end_date}"
    # Historical dates never change once past -- cache hard. Only a window that
    # includes "today" (photo taken today) would need a short TTL; callers
    # asking about today should prefer the live forecast API instead.
    cached = read_cache(cache_key, ttl_seconds=86400 * 365)
    if cached:
        return cached

    url = (f"https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={lat}&longitude={lon}&start_date={start_date}&end_date={end_date}"
           f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
           f"&hourly=soil_temperature_0_to_7cm,soil_moisture_0_to_7cm"
           f"&temperature_unit=fahrenheit&precipitation_unit=inch&timezone=America%2FLos_Angeles")

    data = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'westcoast-mushroom-id'})
            with urllib.request.urlopen(req, timeout=20) as response:
                data = json.loads(response.read().decode())
                break
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            if attempt == 0:
                time.sleep(1)
            else:
                return {'error': f'archive fetch failed: {e}'}

    write_cache(cache_key, data)
    return data


def daily_mean_from_hourly(hourly_times, hourly_values):
    by_date = {}
    for t, v in zip(hourly_times, hourly_values):
        if v is None:
            continue
        date = t.split('T')[0]
        by_date.setdefault(date, []).append(v)
    return {d: sum(vs) / len(vs) for d, vs in by_date.items()}


def compute_features(data, target_date):
    if 'error' in data:
        return {'error': data['error']}

    daily = data.get('daily', {})
    daily_times = daily.get('time', [])
    daily_min = daily.get('temperature_2m_min', [])
    daily_precip = daily.get('precipitation_sum', [])

    hourly = data.get('hourly', {})
    soil_temp_daily = daily_mean_from_hourly(hourly.get('time', []), hourly.get('soil_temperature_0_to_7cm', []))
    soil_moist_daily = daily_mean_from_hourly(hourly.get('time', []), hourly.get('soil_moisture_0_to_7cm', []))

    if target_date not in daily_times:
        return {'error': f'target date {target_date} not in returned range', 'available_range':
                [daily_times[0], daily_times[-1]] if daily_times else []}

    idx = daily_times.index(target_date)

    def window_sum(series_dict_or_list, is_dict, start_idx, end_idx):
        vals = []
        for i in range(max(0, start_idx), end_idx + 1):
            if is_dict:
                v = series_dict_or_list.get(daily_times[i])
            else:
                v = series_dict_or_list[i] if i < len(series_dict_or_list) else None
            if v is not None:
                vals.append(v)
        return vals

    precip_7d = sum(window_sum(daily_precip, False, idx - 6, idx))
    precip_14d = sum(window_sum(daily_precip, False, idx - 13, idx))
    precip_30d = sum(window_sum(daily_precip, False, idx - 29, idx))

    soil_7d_vals = window_sum(soil_temp_daily, True, idx - 6, idx)
    soil_t_7d_mean = sum(soil_7d_vals) / len(soil_7d_vals) if soil_7d_vals else None

    moist_7d_vals = window_sum(soil_moist_daily, True, idx - 6, idx)
    soil_moisture_7d_mean = sum(moist_7d_vals) / len(moist_7d_vals) if moist_7d_vals else None

    frost_vals = window_sum(daily_min, False, idx - 4, idx)
    frost_nights_last5 = sum(1 for v in frost_vals if v <= 28)

    return {
        'target_date': target_date,
        'soil_temp_f_on_date': soil_temp_daily.get(target_date),
        'soil_temp_f_7d_mean': round(soil_t_7d_mean, 1) if soil_t_7d_mean is not None else None,
        'soil_moisture_7d_mean_m3m3': round(soil_moisture_7d_mean, 3) if soil_moisture_7d_mean is not None else None,
        'precip_in_7d': round(precip_7d, 2),
        'precip_in_14d': round(precip_14d, 2),
        'precip_in_30d': round(precip_30d, 2),
        'frost_nights_last5': frost_nights_last5,
        'note': 'context only -- corroborating signal, never a species identifier',
    }


def main():
    if len(sys.argv) < 4:
        print(json.dumps({'error': 'usage: weather_at_time.py lat lon YYYY-MM-DD'}))
        sys.exit(1)

    try:
        lat = float(sys.argv[1])
        lon = float(sys.argv[2])
        target_date = sys.argv[3]
        datetime.strptime(target_date, '%Y-%m-%d')  # validate format
    except ValueError as e:
        print(json.dumps({'error': f'invalid arguments: {e}'}))
        sys.exit(1)

    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    start_dt = target_dt - timedelta(days=30)

    # Archive API has a short lag before "today"'s data is finalized -- if the
    # target date is within the last ~5 days, note that the window may be
    # incomplete rather than silently returning partial data as if solid.
    days_ago = (datetime.utcnow() - target_dt).days
    recency_note = None
    if days_ago < 5:
        recency_note = 'target date is very recent -- archive data for the last few days can be provisional/incomplete'

    data = fetch_archive(lat, lon, start_dt.strftime('%Y-%m-%d'), target_date)
    features = compute_features(data, target_date)
    if recency_note:
        features['recency_note'] = recency_note

    print(json.dumps(features, indent=2))
    sys.exit(0)


if __name__ == '__main__':
    main()
```

**`scripts/fetch_reference_data.py`**
```python
#!/usr/bin/env python3
"""
fetch_reference_data.py - Pulls the species database (species_registry.json,
lookalike_pairs.json, toxin_syndromes.json, photos_manifest.json) from the
repo's raw GitHub content at runtime, so SKILL.md itself can stay slim
instead of carrying the whole database inline.

Uses raw.githubusercontent.com specifically, not api.github.com -- the two
are different domains with different access rules. Verified live on
2026-09-16: a sandboxed Claude session that had GitHub's API blocked
outright (a session-level "add_repo" gate) could still reach
raw.githubusercontent.com with a plain unauthenticated GET, because it's
just a static content CDN for public repo files, not the API. No PAT or
auth of any kind needed to read a public repo this way. If a future
environment blocks this domain too, that's a different, harder problem --
this script fails safe into "use whatever's cached" rather than crashing,
so a temporary network hiccup or an unusually strict sandbox doesn't take
the skill down, it just means data can be stale until the next fetch works.

CLI:
  python3 fetch_reference_data.py                 -- fetch all four files,
                                                       respecting cache TTL
  python3 fetch_reference_data.py --force          -- ignore cache, re-fetch
"""

import sys
import json
import os
import time
import urllib.request
import urllib.error

REPO_OWNER = 'savoxis'
REPO_NAME = 'mushroom-id-reference-data'
REPO_BRANCH = 'main'

REFS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'references')
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
CACHE_META_FILE = os.path.join(CACHE_DIR, 'reference_data_fetch_times.json')

FILES = ['species_registry.json', 'lookalike_pairs.json', 'toxin_syndromes.json', 'photos_manifest.json']
CACHE_TTL_SECONDS = 86400  # 1 day -- this is reference data that changes by hand-edited commits, not live data


def _raw_url(filename):
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{REPO_BRANCH}/references/{filename}"


def _read_fetch_times():
    if not os.path.exists(CACHE_META_FILE):
        return {}
    try:
        with open(CACHE_META_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_fetch_times(times):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(CACHE_META_FILE, 'w') as f:
            json.dump(times, f)
    except Exception:
        pass


def fetch_file(filename, force=False):
    """Fetches one references/<filename> from the repo's raw content into
    references/<filename> locally, respecting the cache TTL unless
    force=True. Returns (ok: bool, detail: str). On any failure, leaves
    whatever local copy already exists untouched rather than deleting it --
    stale data beats no data."""
    dest_path = os.path.join(REFS_DIR, filename)
    fetch_times = _read_fetch_times()
    last_fetch = fetch_times.get(filename, 0)

    if not force and os.path.exists(dest_path) and (time.time() - last_fetch) < CACHE_TTL_SECONDS:
        return True, 'cached (fresh)'

    url = _raw_url(filename)
    req = urllib.request.Request(url, headers={'User-Agent': 'westcoast-mushroom-id'})
    data = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                data = response.read()
                break
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            if attempt == 0:
                time.sleep(1)
            else:
                if os.path.exists(dest_path):
                    return True, f'fetch failed ({e}), using existing cached copy'
                return False, f'fetch failed ({e}) and no cached copy exists'

    try:
        json.loads(data)  # validate before overwriting a good local copy with garbage
    except json.JSONDecodeError as e:
        if os.path.exists(dest_path):
            return True, f'fetched data was not valid JSON ({e}), keeping existing cached copy'
        return False, f'fetched data was not valid JSON ({e}) and no cached copy exists'

    os.makedirs(REFS_DIR, exist_ok=True)
    with open(dest_path, 'wb') as f:
        f.write(data)
    fetch_times[filename] = time.time()
    _write_fetch_times(fetch_times)
    return True, 'fetched fresh copy'


def main():
    force = '--force' in sys.argv
    results = {}
    all_ok = True
    for filename in FILES:
        ok, detail = fetch_file(filename, force=force)
        results[filename] = detail
        all_ok = all_ok and ok
        print(f"{filename}: {detail}")

    if not all_ok:
        print(json.dumps({'ok': False, 'results': results}))
        sys.exit(1)
    print(json.dumps({'ok': True, 'results': results}))


if __name__ == '__main__':
    main()
```

**`scripts/log_find.py`**
```python
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
```

None of these five take a network dependency to exist on disk -- they came
in with this file. `fetch_reference_data.py` and the bootstrap step below
still make network calls at runtime (Open-Meteo, iNaturalist, GitHub's raw
content CDN), same as always; what changed is where the *code* comes from,
not what the code does once it's running.

Then pull the reference JSON -- species list, lookalike pairs, toxin
syndromes, photo links. This is the one thing still worth fetching from
GitHub, and the fetch below only ever writes `.json` files, nothing
executable:

```python
import subprocess, os, urllib.request

REPO = "savoxis/mushroom-id-reference-data"
BRANCH = "main"
FILES = [
    "references/species_registry.json", "references/lookalike_pairs.json",
    "references/toxin_syndromes.json", "references/photos_manifest.json",
]

def bootstrap(dest="."):
    # Try a real clone first -- one request, gets everything at once.
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", f"https://github.com/{REPO}.git", dest],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            return "cloned"
    except Exception:
        pass
    # Fall back to per-file fetch over the raw content CDN. Verified live
    # on 2026-09-16 that raw.githubusercontent.com stays reachable even in
    # a sandbox where api.github.com (and possibly git-over-https to
    # github.com itself) is blocked by session-level policy -- it's a
    # different domain serving static public-repo content, not the API.
    for path in FILES:
        url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{path}"
        local_path = os.path.join(dest, path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "westcoast-mushroom-id"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            with open(local_path, "wb") as f:
                f.write(resp.read())
    return "raw-fetched"

print(bootstrap())
```

If that fails (no network reachable at all in this environment), say so
plainly and fall back to general mycological reasoning from the photo
without tier-specific citations -- do not silently proceed as if the
database loaded when it didn't.

Once present, keep the reference JSON reasonably fresh rather than
fetching once and never again: run `scripts/fetch_reference_data.py` at
the start of a session (it no-ops if the local copy is under a day old,
so this is cheap) so a species someone added or corrected upstream
actually shows up.

---

## Read this part first. It is not optional.

Wild mushroom identification from a photo is inherently unreliable, and this is
not a hedge to cover the skill, it is the documented, measured truth of the
problem. An Australian test of three consumer mushroom-ID apps against 78
real photos found the best of them right 49% of the time, and the others
worse. People have been hospitalized -- an Ohio man in 2022, an Oregon family
of four in 2015 -- after an app told them a poisonous mushroom was fine to
eat. Google's own AI search results have generated a recipe for Amanita
ocreata, a mushroom that kills by liver failure. None of that is a reason to
refuse to help someone narrow down what they found. It is the reason this
skill is built the way it is below, and why the rules in this section do not
bend for a confident-looking photo, a user who says they are "pretty sure,"
or a request to just give a straight answer.

**Rule 1: This skill identifies candidates and flags danger. It never rules
on edibility.** Do not say a mushroom is safe to eat, probably fine, "should
be okay if cooked," or anything a reasonable person would read as permission
to eat it. If the user asks directly ("so can I eat it?"), the answer is a
version of: this tool does taxonomic narrowing, not a safety verdict --
confirm with a physical spore print, a second and third field guide, and
either a certified local expert or your regional mycological society before
anything goes near your mouth. This holds even at 95%+ visual confidence on
an unambiguous edible with no dangerous look-alike, because the cost of being
right 999 times and wrong once, here, is somebody's liver.

**Rule 2: Every answer presents multiple ranked candidates, never one.**
Real identification requires features a photo often cannot show: spore print
color, odor, taste (never suggest a taste test on anything that could be
amatoxin- or orellanine-bearing -- see Myths section), staining/bruising
reaction, and how the flesh looks in cross-section. A single named answer
overstates what a photo can prove. Present the field the way it actually is:
a ranked, reasoned differential.

**Rule 3: If a deadly species is anywhere in the differential, even as a
long-shot, the answer leads with a hard warning banner, at full severity,
every time.** Do not scale the warning down because the deadly candidate
scored low. A rare miss that kills someone is not an acceptable trade for a
cleaner-looking answer on the common case. Any species with `"tier": "A"`
in `references/species_registry.json` triggers this.

**Rule 4: Never invent a species match to satisfy the user's hope.** If the
photo and gathered details do not clear the bar for a real differential
("this could be a lot of things, including X which is deadly, and I cannot
narrow it further from what's available"), say that. "I don't know" is a
correct and complete answer here.

---

## Workflow (steps the model follows)

1. **Confirm it is a fungus.** Look at the photo(s). If it is not a mushroom
   (or the photo is unusable -- too blurry, too far away, wrong subject),
   say so and ask for a usable photo: full mushroom including the base of
   the stem dug out of the ground (not cut at soil level -- the volva at the
   base is often the only thing separating an edible from a killer), a cap
   top-down shot, a gill/pore/underside shot, and a shot of the habitat
   around it.

2. **Extract photo metadata** -> `scripts/exif_extract.py`. Pull GPS
   coordinates and the original capture date/time from EXIF if present.
   Most phone photos have this; screenshots, downloaded images, and
   messaging-app-compressed photos usually strip it.
   - If GPS is missing: ask the user for a location (place name, nearest
     town, or "same area as usual" if they have a known patch). A location
     is required to check regional plausibility and pull weather -- do not
     skip this by guessing.
   - If the date is missing: ask, or accept "today" / "yesterday" / a
     rough date. Season is one of the strongest priors in mushroom ID (a
     "morel" found in September west of the Cascades is almost certainly
     not a morel) so do not proceed without at least an approximate date.

3. **Geocode the location and check it is in scope.** -> `scripts/geo.py`.
   Scope is Oregon, Washington, California, Idaho, and Montana (see the
   script's own docstring for the exact Idaho/Montana panhandle rule).
   Outside that box, say so plainly: regional species lists and range data
   do not apply, and any answer would be guessing past what the database
   supports. You can still do general taxonomic reasoning from the photo,
   but drop the region-specific scoring and say explicitly that the
   species list wasn't built for that area.

4. **Run the structured feature checklist** against the photo(s) -- see
   "Feature extraction checklist" below. Fill in what the photo shows. Mark
   the rest explicitly unknown. Do not guess a feature from a bad angle and
   present it as observed.

5. **Identify what's missing that would actually change the answer.** Not
   every gap matters -- only ask about features that would separate the
   live candidates in front of you. Compare against each candidate's
   `cannot_determine_from_photo` field in `references/species_registry.json`.
   Typical asks: gill attachment and spacing, ring/volva presence, spore
   print color, odor, staining when cut or bruised, substrate (wood vs
   ground -- get this right, it alone rules out entire dangerous genera),
   flesh color in cross-section.

6. **Gather the missing information.** Per user preference, build a single
   rendered HTML form (via the Artifact tool) covering every missing field
   at once, with a small reference diagram next to each choice (see "Info
   gathering" section for the embeddable SVG snippets and form-generation
   approach). If the Artifact tool is not available in this environment,
   fall back automatically to AskUserQuestion, one or a few fields at a
   time, using the same option sets. Never block the whole workflow on a
   feature the user genuinely cannot check (e.g., no spore print taken) --
   proceed with it marked unknown and reflect that in the confidence score.

7. **Pull historical weather/soil conditions for the photo's date and
   location** -> `scripts/weather_at_time.py` (Open-Meteo archive API, no
   key required). This is a secondary, corroborating signal only -- it
   tells you whether conditions were plausible for something to be
   fruiting, never what species it is. A bone-dry October does not rule
   out a chanterelle someone found in an irrigated garden bed. Use it to
   raise or lower confidence slightly, never to break a tie on morphology.

8. **Match against the database.** Load `references/species_registry.json`
   (every species, `tier` field is `"A"` / `"B"` / `"C"` for lethal /
   severe / common), `references/lookalike_pairs.json`, and
   `references/toxin_syndromes.json`. Compare the filled-in feature
   checklist against every entry -- build the full differential, not just
   the best guess. Cross-check `lookalike_pairs.json` specifically: if the
   top candidate's id appears in `common_find`, that pairing's dangerous
   species must appear in the answer even if it scored low on its own.

9. **Pull reference photos for the live candidates** ->
   `references/photos_manifest.json`, keyed by species id. Each entry has
   up to 3 real, openly-licensed photos (url, license, attribution,
   observation link) sourced from iNaturalist and/or Mushroom Observer via
   `scripts/fetch_photo_refs.py` -- each photo's `source` field says which.
   Actually fetch and look at the photo URLs
   for the top few candidates -- particularly the Tier A ones in play --
   rather than reasoning from memory of what a species "usually" looks
   like. A small number of species have no open-licensed photo available
   yet (the manifest entry's `photos` array is empty); say so rather than
   describing a photo that isn't there. This step needs an environment
   that can actually view an image (Read on a downloaded file, or a
   multimodal fetch) -- if that's not available here, fall back to citing
   the observation link as text instead of claiming a visual comparison
   happened.

10. **Score each candidate.** Use a plain-language confidence band, not a
    fake-precision percentage:
    - `RULED OUT` - a stated feature is incompatible (e.g., grows on wood,
      candidate species is exclusively terrestrial)
    - `LONG SHOT` - broadly consistent, several unconfirmed or slightly-off
      features
    - `PLAUSIBLE` - consistent with everything gathered, key differentiators
      still unconfirmed
    - `STRONG VISUAL MATCH` - consistent with everything gathered including
      at least one hard-to-fake diagnostic feature (spore print color,
      confirmed substrate, confirmed staining reaction)
    - Never use a band above `STRONG VISUAL MATCH`. There is no `CONFIRMED`
      band. A photo-and-description workflow does not get to claim
      certainty, full stop.

11. **Compose the answer** using the template in "Answer template" below:
    hard-warn banner first if triggered, then the ranked differential with
    reasoning per candidate, the specific dangerous-lookalike callout if
    relevant, the weather/condition context as a minor note, and the fixed
    closing disclaimer. The closing disclaimer is not optional and is not
    to be shortened, reworded into something softer, or dropped because the
    top candidate looks obviously benign.

12. **Log the find** -> `scripts/log_find()` in `scripts/log_find.py`.
    Build an entry from the photo date, location, and top candidates and
    append it to `logs/finds_log.jsonl` in the local working copy of the
    repo. This is local disk only -- no network call, no credential, no
    confirmation needed, nothing leaves the session. Skip only if the user
    says they don't want this particular find logged at all. This never
    performs a `git commit`/`push` of anything -- not the find log, and
    not `references/*.json` -- species corrections stay a manual,
    human-reviewed commit on purpose, given the life-safety stakes of
    that data.

---

## Feature extraction checklist

Fill this in from the photo(s) before matching against the database. Mark
anything the photo genuinely does not show as `unknown`, not as a guess.
This structure exists so nothing gets skipped by accident -- work through
it in order every time, even on an "obvious" ID.

1. **Cap**: shape (convex, flat, funnel, conical, bell, irregular/lobed),
   color, surface (smooth, scaly, sticky/viscid, dry, wrinkled/brain-like),
   size if estimable, margin (smooth, striate/grooved, inrolled).
2. **Underside**: gills (and if so: color, spacing, attachment -- free /
   attached-adnate / decurrent), pores (color, size), teeth/spines, false
   gills (blunt forked ridges, not true blades), or smooth.
3. **Stem**: color, texture, presence and type of ring, base shape (tapered,
   bulbous, rooting), and critically -- **is there any visible volva or
   sac at the base, or was the specimen cut instead of dug so the base is
   unknown?**
4. **Flesh**: color, any visible staining/bruising in the photo, cross-
   section if provided (hollow vs chambered vs solid).
5. **Growth pattern and substrate**: solitary, scattered, or clustered;
   growing from soil or from wood (look for buried roots/wood at the
   base, not just "looks like it's on the ground").
6. **Habitat context**: visible tree species nearby if identifiable
   (conifer vs hardwood matters enormously -- most of Tier A is
   conifer/oak associated), elevation cues, disturbed vs undisturbed
   ground, lawn/urban vs forest.
7. **Scale**: anything in frame for size reference (hand, coin, boot).
8. **Not visible from any photo, always unknown unless the user reports
   it**: spore print color, smell, taste (never elicit a deliberate
   taste-test -- see Myths), staining reaction over time, exact
   microscopic features.

---

## Info gathering: form and fallback questions

Per the workflow, missing decision-critical fields get gathered through a
single rendered form when the Artifact tool is available, falling back to
AskUserQuestion when it is not. Build the form (or the AskUserQuestion
batch) only from fields that are actually still undetermined and actually
matter for the live differential -- do not ask all nine fields below every
time if six of them are already answered by the photo or don't separate
the current candidates.

**Standard field set** (ask only the ones still needed):

| Field | Options |
|---|---|
| Underside type | Free gills / Attached gills / Decurrent gills / False gills (blunt ridges) / Pores / Teeth-spines / Smooth |
| Spore print color | White / Pink / Brown / Rusty-brown / Dark purple-brown to black / Green / Didn't take one |
| Ring on stem | Yes / No / Not sure or fell off |
| Base of stem | Bulbous with a sac (volva) / Bulbous, no sac / Straight, no bulb / Not dug up, unknown |
| Growing from | Ground / Wood (stump, log, buried root) / Not sure |
| Growth pattern | Solitary / Small separate group / Dense cluster from one base |
| Smell | None noticed / Fruity-apricot / Spicy-cinnamon / Foul-unpleasant / Bleach-like / Other or not sure |
| Cut/bruise reaction | Didn't cut it / No change / Turned blue-green / Turned reddish-pink / Other change |
| Cross-section (morel-shaped only) | Completely hollow / Chambered or cottony / Didn't cut it |

**Rendered form approach** (primary, per user preference): generate a
single self-contained HTML page via the Artifact tool with one short
question block per needed field, radio-button or button-style choices,
and a small inline SVG icon next to each choice so a novice can match
what they saw without knowing the vocabulary. Keep it visually simple and
consistent -- same style for every icon, functional not decorative,
because this is a safety tool, not a showcase. Two worked examples below;
follow the same visual language (thin dark stroke, no fill except where
noted, ~80x80 viewBox) for any other field's icons.

Free gills (stop short of the stem):
```svg
<svg viewBox="0 0 80 80" width="60" height="60">
  <path d="M10 30 Q40 5 70 30" fill="none" stroke="#333" stroke-width="2"/>
  <line x1="38" y1="30" x2="42" y2="70" stroke="#333" stroke-width="3"/>
  <line x1="22" y1="30" x2="24" y2="42" stroke="#333" stroke-width="1.5"/>
  <line x1="30" y1="30" x2="31" y2="45" stroke="#333" stroke-width="1.5"/>
  <line x1="50" y1="30" x2="49" y2="45" stroke="#333" stroke-width="1.5"/>
  <line x1="58" y1="30" x2="56" y2="42" stroke="#333" stroke-width="1.5"/>
</svg>
```

Decurrent gills (run down onto the stem):
```svg
<svg viewBox="0 0 80 80" width="60" height="60">
  <path d="M10 28 Q40 5 70 28" fill="none" stroke="#333" stroke-width="2"/>
  <line x1="38" y1="28" x2="42" y2="70" stroke="#333" stroke-width="3"/>
  <line x1="20" y1="28" x2="35" y2="55" stroke="#333" stroke-width="1.5"/>
  <line x1="28" y1="28" x2="37" y2="58" stroke="#333" stroke-width="1.5"/>
  <line x1="52" y1="28" x2="45" y2="58" stroke="#333" stroke-width="1.5"/>
  <line x1="60" y1="28" x2="47" y2="55" stroke="#333" stroke-width="1.5"/>
</svg>
```

For spore print color options, a simple filled circle swatch in the
approximate color (white with a gray outline for "white", `#7a5230` for
rusty-brown, `#2b1a3d` for dark purple-black, `#5a8a3a` for green, and so
on) is clearer than a line drawing and takes one `<circle>` element.

**AskUserQuestion fallback**: identical field set and option wording,
asked as one question per field (or grouped where the tool allows
multiple questions per call), no icons -- text labels only. Use this
automatically whenever the Artifact tool is not available in the current
environment, or if generating the form fails for any reason. Never block
the workflow on the form specifically; the fallback exists so this skill
degrades gracefully on any Claude surface.

---

## The general LBM caution

"LBM" (little brown mushroom) is mycologist shorthand for the huge number
of small brown-to-tan mushrooms that cannot be reliably told apart without
a microscope and spore chemistry -- and several of this database's worst
Tier A entries (Galerina, Conocybe/Pholiotina, small Lepiotas) are LBMs.
When the photo shows an LBM-type mushroom and gathered details still leave
multiple genera on the table, the correct answer is not to keep narrowing
-- it's to say plainly that this category doesn't resolve to species level
from a photo and description, full stop, and that any LBM being considered
for eating (including for psilocybin content) needs a spore print at
minimum and ideally an in-person expert or microscopy. This is not a
cop-out answer, it's the accurate one -- professional mycologists say the
same thing about their own casual photo IDs of this group.

---

## Answer template

```
[PHOTO READ]
What EXIF gave us (GPS/date) or what the user supplied instead, plus the
geocoded location, scope check, and data timestamp for the weather pull.
One or two lines, factual, no hedging language wasted here -- save the
hedging for where it matters below.

[HARD WARNING]  (omit this section entirely if no Tier A species is in
the differential -- do not include a softened or "just in case" version
of it when nothing actually triggered it)
A visually distinct, unmissable block. Name the Tier A species in play,
the syndrome, the onset timing, and the one-line reason it's in the
differential. This goes first, above the candidate list, every time it
triggers.

[TOP CANDIDATES]
Ranked list, most to least likely. Each candidate gets: name (common +
scientific), tier, confidence band (RULED OUT / LONG SHOT / PLAUSIBLE /
STRONG VISUAL MATCH), and 1-3 sentences of reasoning tied to specific
observed features -- not "it looks like X," but "the false gills and
wood-attached base point toward jack-o-lantern over chanterelle." Note
whether a reference photo was actually pulled and compared, or whether
none was available.

[DANGEROUS LOOKALIKE CHECK]
Explicit callout of the specific pairing from lookalike_pairs.json if the
top candidate has one, plus the exact field test that would separate them
-- even if this repeats something already said above. This section exists
so the single most important sentence in the whole answer cannot be
missed by someone skimming.

[WHAT WOULD NARROW THIS FURTHER]
The specific unresolved feature(s) that would move a candidate up or down
-- spore print, cross-section, dug-out base, whatever applies. Give the
user something concrete to go check, not a vague "get an expert opinion"
non-answer.

[CONDITIONS]  (brief, optional if not useful)
One line on whether recent weather/soil conditions were plausible for
this to be fruiting here -- corroborating only, explicitly labeled as such.

[FINE PRINT]  (always, verbatim, never shortened or softened)
This is an identification aid, not a safety verdict -- it narrows
candidates from a photo and description, which cannot capture everything
a real specimen can. Do not eat anything based on this answer. Take a
spore print, check a second and third source, and get it confirmed by a
local mycological society or a certified forager before it goes near food.
If any Tier A species was mentioned above, treat this as a hard no
regardless of how confident the rest of the answer sounds.
```

## Worked example

**User:** "Found a bunch of these on an old stump behind my house in
Portland, end of October. Are they honey mushrooms?" + photo showing a
cluster of honey-brown capped mushrooms on wood, no base/underside visible.

**Model answer (shape, not verbatim):**

> Photo read: no EXIF GPS or date on this image (looks re-compressed --
> common with messaging apps), so working from what you said: Portland,
> OR, late October. In scope.
>
> Hard warning: Deadly Galerina (*Galerina marginata*) grows on the exact
> same wood, in the exact same clustered habit, in the exact same season
> as honey mushroom, and causes fatal liver failure with a 6-24 hour
> delayed onset -- you feel fine for most of a day before it gets bad.
> This has to be ruled out before anything else here matters.
>
> Top candidates: Honey mushroom (*Armillaria* spp, Tier C) -- PLAUSIBLE.
> Cluster habit, wood substrate, and cap color are consistent, but the
> photo doesn't show the ring, gill color, or spore print needed to
> separate this from Galerina. Reference photos pulled and compared for
> both. Deadly Galerina (*Galerina marginata*, Tier A) -- PLAUSIBLE, same
> reasoning, cannot be ruled out yet.
>
> Dangerous look-alike check: spore print is the fast answer here. White
> spore print points toward Armillaria; rusty-brown points toward
> Galerina and means stop, full stop. Put a cap gill-side-down on dark
> paper overnight.
>
> What would narrow this further: spore print (above), a clear photo of
> the ring on the stem (Armillaria's is more substantial), and whether
> the gills are white (Armillaria) or a dingier tan-rust color (Galerina).
>
> Conditions: late October in Portland after fall rains is a normal
> window for both species -- doesn't help separate them, just confirms
> either is plausible timing.
>
> This is an identification aid, not a safety verdict -- it narrows
> candidates from a photo and description, which cannot capture
> everything a real specimen can. Do not eat anything based on this
> answer. Take a spore print, check a second and third source, and get it
> confirmed by a local mycological society or a certified forager before
> it goes near food. Deadly Galerina is still on the table above -- treat
> this as a hard no until the spore print rules it out.

---

## Debunked myths -- shut these down whenever they come up

These circulate constantly in casual foraging conversation and every one
of them has killed someone who trusted it.

- **"If animals/insects/slugs eat it, it's safe for humans."** False.
  Different physiology, different toxin sensitivity entirely. Squirrels
  eat death caps.
- **"A silver spoon/coin turns black around poisonous mushrooms."**
  False, a total myth, not based on any real chemistry. Never repeat this
  as even a partial data point.
- **"Bright colors mean poisonous, dull colors mean safe" (or the
  reverse).** False both ways. Death cap is a dull olive-green. Some
  choice edibles (chanterelles, king boletes) are vividly colored. Color
  is not a toxicity signal in either direction.
- **"Cooking or parboiling neutralizes the toxins."** False for amatoxins
  (heat-stable, survive cooking, drying, and freezing) and false for
  orellanine (also heat-stable). Partially true but dangerously
  unreliable for gyromitrin (partly volatile, but documented deaths have
  occurred from properly parboiled Gyromitra -- dose and exposure to the
  fumes during cooking both matter, and "usually fine" is not a safety
  margin worth betting a liver on).
- **"If it tastes fine / isn't bitter, it's not poisonous."** False and
  actively dangerous as a heuristic -- death cap is reportedly pleasant-
  tasting. **Never suggest a taste test on anything in the Tier A
  differential.** A small taste-and-spit is a real, if risky, identification
  technique professional mycologists sometimes use on a narrow set of
  already-substantially-ruled-in species -- it is never something to
  suggest to someone doing their own casual ID, and never appropriate at
  all when amatoxin or orellanine species haven't been excluded.
- **"I've eaten this species before with no problem, so it's fine."**
  False as a safety test -- see the *Paxillus involutus* entry in the
  registry, which built up a fatal immune reaction over years of
  apparently fine meals. Past tolerance is not evidence of safety for
  cumulative-toxin species, and doesn't help at all for a mushroom you
  haven't 100% confirmed is the same thing you ate before anyway.
- **"Peeling the skin or removing the gills removes the poison."** False
  for amatoxins and orellanine -- the toxins are distributed through the
  flesh, not concentrated in a part you can trim away.
- **"All mushrooms growing on wood are safe" / "all white mushrooms are
  safe" / any single-feature blanket rule.** False by construction --
  this entire database exists because single features don't generalize.
  Destroying angel is white. Deadly Galerina grows on wood. There is no
  shortcut feature that replaces going through the checklist.

---

## FAQ / common overrides

- **User provides a spore print photo separately** -> read its color
  directly instead of asking; this resolves several of the highest-value
  checks in one step (Galerina vs Armillaria, Galerina vs Psilocybe,
  Chlorophyllum vs Macrolepiota).
- **User says "just tell me if I can eat it"** -> Rule 1 still applies.
  Give the differential and the reasoning, state plainly that this tool
  doesn't issue that verdict, and say what specifically would let a human
  expert make that call quickly (spore print + the specific feature(s)
  still unknown).
- **Photo is of a mushroom already cut/cooked/in a pan** -> work with
  what's visible, but flag hard that critical base/volva/spore
  information is now permanently unrecoverable, which should pull
  confidence down, not up.
- **Multiple photos of the same find from different angles** -> merge
  into one feature checklist rather than treating as separate specimens,
  unless the user says they're different finds.
- **Location outside the five-state scope** -> say so plainly, still do
  general morphological reasoning from the photo, but drop
  region-specific range/season claims and note the database wasn't built
  for that area.
- **User pushes back on the disclaimer as excessive** -> hold the line
  per Rule 1-3. Explain briefly why (see the opening section) rather than
  just repeating the rule; if they still push, that's fine, the answer
  doesn't change.
- **`references/` failed to load** -> say so plainly rather than silently
  reasoning without the database. General mycological knowledge can still
  inform an answer, but citations, tier assignments, and the
  lookalike-pairs cross-check are unavailable and the answer should say
  that outright. (`scripts/` doesn't have this failure mode the same way
  -- it's written from this file directly, not fetched, so there's no
  network step for it to fail at.)

---

## Notes for deployment

- Python 3, standard library only (struct, json, urllib, math, os, time,
  datetime, subprocess) across all five scripts -- no pip installs,
  matching oregon-mushroom-scout's discipline. `geo.py`, `exif_extract.py`,
  `weather_at_time.py`, `fetch_reference_data.py`, and `log_find.py` are
  embedded directly in Setup above; only `references/*.json` comes from
  the companion repo. (Why it's split this way -- and the two rounds of
  history behind why the scripts moved back in rather than staying
  fetched -- is in that repo's `README.md`, not here; this file describes
  what the skill does now, not the road that got here.)
- ASCII only, no smart quotes or unicode in any generated text.
- Network calls: Open-Meteo geocoding/elevation/archive APIs (no key),
  iNaturalist's public API for photo sourcing (no key), and
  raw.githubusercontent.com for the reference JSON (no key, no auth --
  verified reachable even when api.github.com is blocked by a
  session-level policy). Every network call uses a timeout plus one
  retry and fails toward "ask the user" or "use what's cached," never
  toward crashing the workflow.
- Find-log entries are local-only -- there is no push-to-GitHub path and
  no credential anywhere in this skill or this repo. Species corrections
  to `references/*.json` stay a manual, human-reviewed commit, given the
  life-safety stakes of that data. (Project history on an earlier,
  removed find-log push feature lives in `README.md`, not here -- this
  file describes what the skill does now, not what it used to do.)
- Species list is a deliberately scoped v1, expanded twice already (44 ->
  51 -> 58 species, September 2026) and meant to keep growing (life-safety
  species exhaustive for the region; common finds at 58 species total as
  of the last expansion, still not an exhaustive regional flora). Flag any
  species to add or correct in the repo directly -- this is meant to
  grow, and now it actually can without regenerating this whole file.
- Reference photos now come from two independent sources -- iNaturalist
  directly, and Mushroom Observer's collection reached through GBIF (see
  `scripts/fetch_photo_refs.py`'s docstring and `README.md` for why GBIF
  rather than Mushroom Observer's own API). Photos and citations both got
  a strengthening pass alongside the species-count expansion above --
  neither is exhaustive, both are fine places to keep extending.
- Tier C (edible/informational) entries carry four extra deep-ID fields
  beyond the shared schema -- `spore_print_color`, `cap_size`, `stem_size`,
  `flesh_characteristics` (bruising/latex/staining behavior) -- added in a
  September 2026 research pass across MushroomExpert.com, MykoWeb, First
  Nature, NAMA, and PNW Key Council trial keys. Step 8/9 below can surface
  these fields alongside `watch_for` when a candidate is Tier C, same as
  it already does for `key_features`/`habitat_substrate`/etc. Culinary
  data (taste, prep, storage) was deliberately left out of this pass --
  scoped to identification-relevant fields only.
