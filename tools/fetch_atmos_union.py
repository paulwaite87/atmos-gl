#!/usr/bin/env python3
"""
fetch_atmos_union.py — Standalone GFS filtered downloader.

Give it the FULL URL to a GFS pgrb2.0p25 file, e.g.

  https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.20260613/18/atmos/gfs.t18z.pgrb2.0p25.f000

It reads the .idx sidecar, resolves the byte ranges for exactly the ATMOS_TARGETS
your data_collector uses, downloads only those ranges, and writes a small filtered
"union" GRIB locally — the same bytes the collector hands to the unpackers.

This is a faithful, dependency-light copy of worldmap.lib.gfs.{gfs_index_ranges,
download_byte_ranges}; nothing project-internal is imported, so you can run it
anywhere Python 3 + requests is available.

Usage:
  python3 fetch_atmos_union.py <FULL_GRIB_URL> [-o out.grib2] [--list]

  --list   also print the .idx lines that matched each target (handy for seeing
           the exact shortName/level the file actually uses).

Examples:
  python3 fetch_atmos_union.py \
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.20260613/18/atmos/gfs.t18z.pgrb2.0p25.f000"

  # AWS mirror (often faster, no rate limits):
  python3 fetch_atmos_union.py \
    "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20260613/18/atmos/gfs.t18z.pgrb2.0p25.f000" \
    -o union_f000.grib2 --list
"""

import sys
import argparse

try:
    import requests
except ImportError:
    sys.exit("This script needs 'requests'.  pip install requests")


# Must match worldmap.lib.gfs.ATMOS_TARGETS exactly.
ATMOS_TARGETS = [
    ":PRMSL:mean sea level:",
    ":PRATE:surface:",
    ":TOZNE:",
    ":CAPE:surface:",
    ":CIN:surface:",
    ":TMP:2 m above ground:",
    ":UGRD:10 m above ground:",
    ":VGRD:10 m above ground:",
]


def gfs_index_ranges(grib_url, targets, timeout=30, want_lines=False):
    """Resolve (start, end) byte ranges for each target from the .idx sidecar.

    Mirrors worldmap.lib.gfs.gfs_index_ranges. Returns a list of (start, end)
    tuples (end < 0 means 'to EOF'). If want_lines, also returns the matched
    .idx line text per target as a parallel list.
    """
    if not targets:
        return ([], []) if want_lines else []
    r = requests.get(grib_url + ".idx", timeout=timeout)
    r.raise_for_status()
    lines = r.text.strip().split("\n")
    ranges = []
    matched = []
    for target in targets:
        hit = None
        for i, line in enumerate(lines):
            if target in line:
                start = int(line.split(":")[1])
                end = (int(lines[i + 1].split(":")[1]) - 1
                       if i + 1 < len(lines) else -1)
                ranges.append((start, end))
                hit = line
                break
        matched.append(hit)  # None if this target wasn't in the sidecar yet
    if want_lines:
        return ranges, matched
    return ranges


def download_byte_ranges(url, ranges, timeout=120):
    """Download the given byte ranges and return the concatenated bytes.

    Mirrors worldmap.lib.gfs.download_byte_ranges.
    """
    out = bytearray()
    for start, end in ranges:
        hdr = {"Range": f"bytes={start}-" if end < 0 else f"bytes={start}-{end}"}
        r = requests.get(url, headers=hdr, timeout=timeout, stream=True)
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            out += chunk
    return bytes(out)


def default_outname(url):
    """Derive a sensible output filename from the URL's basename."""
    base = url.rstrip("/").split("/")[-1]
    if not base:
        base = "gfs_atmos_union"
    # gfs.t18z.pgrb2.0p25.f000 -> gfs.t18z.pgrb2.0p25.f000.union.grib2
    if not base.endswith(".grib2"):
        base = base + ".union.grib2"
    return base


def main():
    ap = argparse.ArgumentParser(
        description="Download a GFS pgrb2 file filtered to ATMOS_TARGETS (byte-range union)."
    )
    ap.add_argument("url", help="Full URL to the GFS pgrb2.0p25.fFFF file (no .idx suffix).")
    ap.add_argument("-o", "--out", default=None, help="Output filename (default: derived from URL).")
    ap.add_argument("--list", action="store_true",
                    help="Print the matched .idx line for each target.")
    ap.add_argument("--timeout", type=int, default=120, help="Per-request timeout seconds.")
    args = ap.parse_args()

    url = args.url
    out = args.out or default_outname(url)

    print(f"Index:   {url}.idx")
    try:
        ranges, matched = gfs_index_ranges(url, ATMOS_TARGETS, timeout=args.timeout, want_lines=True)
    except requests.HTTPError as e:
        sys.exit(f"Failed to fetch .idx: {e}")
    except requests.RequestException as e:
        sys.exit(f"Network error fetching .idx: {e}")

    found = sum(1 for m in matched if m is not None)
    print(f"Targets: {found}/{len(ATMOS_TARGETS)} resolved from sidecar.\n")

    for target, line in zip(ATMOS_TARGETS, matched):
        status = "OK " if line else "MISS"
        if args.list and line:
            print(f"  [{status}] {target}")
            print(f"          {line}")
        else:
            print(f"  [{status}] {target}")

    if not ranges:
        sys.exit("\nNo byte ranges resolved — sidecar may not be populated yet, "
                 "or the URL/targets don't match. Nothing downloaded.")

    total_bytes = sum((e - s + 1) for s, e in ranges if e >= 0)
    approx = f"~{total_bytes/1e6:.1f} MB" if total_bytes else "unknown size (one open-ended range)"
    print(f"\nDownloading {len(ranges)} byte range(s) ({approx})...")

    try:
        data = download_byte_ranges(url, ranges, timeout=args.timeout)
    except requests.RequestException as e:
        sys.exit(f"Download failed: {e}")

    with open(out, "wb") as f:
        f.write(data)

    print(f"Wrote {len(data)/1e6:.1f} MB -> {out}")
    if found < len(ATMOS_TARGETS):
        print(f"\nNOTE: {len(ATMOS_TARGETS) - found} target(s) were MISSING from the sidecar "
              f"(the freshest hour often lags). The union is still valid for the targets "
              f"that resolved.")


if __name__ == "__main__":
    main()
