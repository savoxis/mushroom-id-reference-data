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
