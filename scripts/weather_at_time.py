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
