#!/usr/bin/env python3
"""Build a minimal synthetic JPEG+EXIF blob with known GPS + DateTimeOriginal,
then run exif_extract.py's logic against it to verify the hand-rolled parser
is actually correct before it goes into the skill."""

import struct
import sys
sys.path.insert(0, '/home/claude/skill-draft')
from exif_extract import extract

def rational(num, den):
    return struct.pack('<II', num, den)

def build_exif_app1():
    endian = '<'  # little-endian (Intel, "II")

    # We'll build IFD0 with: DateTime(0x0132), ExifIFD pointer(0x8769), GPSIFD pointer(0x8825)
    # Then Exif SubIFD with DateTimeOriginal(0x9003)
    # Then GPS IFD with GPSLatitudeRef, GPSLatitude, GPSLongitudeRef, GPSLongitude

    # Known test values
    test_datetime = b'2026:05:24 07:15:00\x00'  # 20 bytes incl null
    lat_ref = b'N\x00'.ljust(4, b'\x00')  # inline value/offset field is always 4 bytes, left-justified
    lon_ref = b'W\x00'.ljust(4, b'\x00')
    # 44 deg 3 min 30.5 sec N ; 121 deg 18 min 45.2 sec W  (Bend, OR area)
    lat_dms = rational(44,1) + rational(3,1) + rational(305,10)
    lon_dms = rational(121,1) + rational(18,1) + rational(452,10)

    tiff_start_marker = b'II' + struct.pack('<H', 42) + struct.pack('<I', 8)  # IFD0 at offset 8

    # We need to lay out data after the IFDs for values that don't fit inline (>4 bytes) or
    # need a fixed 2/4-byte ASCII slot layout. Let's plan offsets carefully.
    # IFD0: 3 entries -> 2 + 3*12 + 4 = 42 bytes, starting at offset 8 -> ends at offset 50
    # DateTime (0x0132) ASCII count=20 -> doesn't fit inline (20>4) -> needs offset
    # ExifIFD pointer (0x8769) LONG count=1 -> fits inline (offset value itself)
    # GPSIFD pointer (0x8825) LONG count=1 -> fits inline

    ifd0_offset = 8
    ifd0_entry_count = 3
    ifd0_size = 2 + ifd0_entry_count*12 + 4
    ifd0_end = ifd0_offset + ifd0_size  # = 50

    datetime_offset = ifd0_end  # place DateTime string right after IFD0
    datetime_len = len(test_datetime)
    after_datetime = datetime_offset + datetime_len

    exif_ifd_offset = after_datetime
    exif_entry_count = 1  # just DateTimeOriginal
    exif_ifd_size = 2 + exif_entry_count*12 + 4
    exif_ifd_end = exif_ifd_offset + exif_ifd_size

    dt_orig_offset = exif_ifd_end
    dt_orig_len = len(test_datetime)
    after_dt_orig = dt_orig_offset + dt_orig_len

    gps_ifd_offset = after_dt_orig
    gps_entry_count = 4  # LatRef, Lat, LonRef, Lon
    gps_ifd_size = 2 + gps_entry_count*12 + 4
    gps_ifd_end = gps_ifd_offset + gps_ifd_size

    lat_dms_offset = gps_ifd_end
    lon_dms_offset = lat_dms_offset + len(lat_dms)

    def entry(tag, typ, cnt, value_or_offset_bytes):
        return struct.pack('<HHI', tag, typ, cnt) + value_or_offset_bytes

    # IFD0
    ifd0 = struct.pack('<H', ifd0_entry_count)
    ifd0 += entry(0x0132, 2, len(test_datetime), struct.pack('<I', datetime_offset))  # DateTime ASCII
    ifd0 += entry(0x8769, 4, 1, struct.pack('<I', exif_ifd_offset))  # Exif IFD pointer
    ifd0 += entry(0x8825, 4, 1, struct.pack('<I', gps_ifd_offset))   # GPS IFD pointer
    ifd0 += struct.pack('<I', 0)  # no next IFD

    # Exif SubIFD
    exif_ifd = struct.pack('<H', exif_entry_count)
    exif_ifd += entry(0x9003, 2, len(test_datetime), struct.pack('<I', dt_orig_offset))  # DateTimeOriginal
    exif_ifd += struct.pack('<I', 0)

    # GPS IFD
    gps_ifd = struct.pack('<H', gps_entry_count)
    gps_ifd += entry(0x0001, 2, 2, lat_ref)  # GPSLatitudeRef ASCII count=2 fits inline
    gps_ifd += entry(0x0002, 5, 3, struct.pack('<I', lat_dms_offset))  # GPSLatitude RATIONAL x3
    gps_ifd += entry(0x0003, 2, 2, lon_ref)  # GPSLongitudeRef
    gps_ifd += entry(0x0004, 5, 3, struct.pack('<I', lon_dms_offset))  # GPSLongitude RATIONAL x3
    gps_ifd += struct.pack('<I', 0)

    tiff = tiff_start_marker
    tiff += ifd0
    tiff += test_datetime      # at datetime_offset
    tiff += exif_ifd           # at exif_ifd_offset
    tiff += test_datetime      # at dt_orig_offset (DateTimeOriginal string)
    tiff += gps_ifd            # at gps_ifd_offset
    tiff += lat_dms            # at lat_dms_offset
    tiff += lon_dms            # at lon_dms_offset

    app1_payload = b'Exif\x00\x00' + tiff
    return app1_payload

def build_jpeg(app1_payload):
    app1_marker = b'\xff\xe1' + struct.pack('>H', len(app1_payload) + 2) + app1_payload
    soi = b'\xff\xd8'
    # minimal SOS-ish tail so our scanner's break-on-0xFFDA still works even though there's no real image
    sos = b'\xff\xda\x00\x02\x00'
    eoi = b'\xff\xd9'
    return soi + app1_marker + sos + eoi

if __name__ == '__main__':
    app1 = build_exif_app1()
    jpeg_bytes = build_jpeg(app1)
    test_path = '/tmp/synthetic_test.jpg'
    with open(test_path, 'wb') as f:
        f.write(jpeg_bytes)

    result = extract(test_path)
    print("RESULT:", result)

    expected_lat = 44 + 3/60 + 30.5/3600
    expected_lon = -(121 + 18/60 + 45.2/3600)

    assert result['has_gps'], "FAIL: GPS not extracted"
    assert result['has_datetime'], "FAIL: datetime not extracted"
    assert abs(result['lat'] - expected_lat) < 0.0001, f"FAIL: lat mismatch {result['lat']} vs {expected_lat}"
    assert abs(result['lon'] - expected_lon) < 0.0001, f"FAIL: lon mismatch {result['lon']} vs {expected_lon}"
    assert result['datetime'] == '2026:05:24 07:15:00', f"FAIL: datetime mismatch {result['datetime']}"
    print("ALL ASSERTIONS PASSED")
    print(f"Expected lat={expected_lat:.6f} lon={expected_lon:.6f}")
