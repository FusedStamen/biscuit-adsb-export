#!/usr/bin/env python3
"""
biscuit_to_wardrift.py - turn the Biscuit ADS-B export (biscuit_adsb.csv) into Wardrift / WDGoWars JSON.

Input:   biscuit_adsb.csv  (made by biscuit_adsb_export.sh)
Output:  one file per UTC day in the output folder:
           adsb_YYYY-MM-DD.json      {"now":..., "aircraft":[{hex, flight, lat, lon, seen_pos, seen}, ...]}
           adsb_YYYY-MM-DD.json.gz   the same, gzipped (the exact body Wardrift's API takes)
Rules (same as the Pi's uploader):
  - every aircraft heard is included; nothing is filtered by signal strength, range or altitude
  - one entry per aircraft per UTC day: its first sighting that day (Wardrift credits one aircraft per UTC day)
  - "now" = time of the newest observation in the file, observation time = now - seen
  - history_style/ holds the same files in the older uConsole layout (now, date, unique_aircraft_count,
    first_seen/last_seen per aircraft), in case the website wants that layout instead
  - Wardrift only credits observations under 72 hours old; older days are still written but are NOT uploaded
  - WDGoWars rejects a file whose aircraft cover too large an area for one receiver (e.g. a flight). When the
    receiver moved, a day is split in time order into several files, each limited to --max-span-km (default 600)
    of north-south or east-west extent: adsb_YYYY-MM-DD_part1.json, _part2.json, ...

Optional direct upload (same request the Pi uses):
  python3 biscuit_to_wardrift.py biscuit_adsb.csv --upload --key-file ~/wardrift.key
The key is read from that file and never printed. Without --upload nothing is sent anywhere.
"""
import argparse
import csv
import glob
import gzip
import json
import math
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = "https://wardrift.net/v1/ingest/signals"
ICAO_RE = re.compile(r"^[0-9a-f]{6}$")
MAX_PER_FILE = 2000   # WDGoWars browser upload limit; Wardrift API accepts more but one limit keeps files uniform


def utc_day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def num(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def load(path):
    first = {}   # (day, icao) -> (ts, callsign, lat, lon)
    extra = {}   # (day, icao) -> dict(alt, gs, track, rssi, last, n) for the history-style file
    rows = bad_icao = bad_time = 0
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows += 1
            icao = (r.get("icao") or "").strip().lower()
            if not ICAO_RE.match(icao):
                bad_icao += 1
                continue
            try:
                ts = int(float(r["timestamp"]))
            except (KeyError, ValueError, TypeError):
                bad_time += 1
                continue
            try:
                lat, lon = float(r.get("lat") or 0), float(r.get("lon") or 0)
            except ValueError:
                lat = lon = 0.0
            cs = (r.get("callsign") or "").strip()
            key = (utc_day(ts), icao)
            ex = extra.setdefault(key, {"n": 0, "last": ts})
            ex["n"] += 1
            ex["last"] = max(ex["last"], ts)
            old = first.get(key)
            if old is None or ts < old[0]:
                first[key] = (ts, cs, lat, lon)
                ex["alt"] = num(r.get("alt_ft"))
                ex["gs"] = num(r.get("speed_kt"))
                ex["track"] = num(r.get("bearing_deg"))
                ex["rssi"] = num(r.get("rssi_dbm"))
            elif not old[1] and cs:      # keep a callsign if a later row has one
                first[key] = (old[0], cs, old[2], old[3])
    return first, extra, rows, bad_icao, bad_time


def extent_km(minlat, maxlat, minlon, maxlon):
    ns = (maxlat - minlat) * 111.2
    ew = (maxlon - minlon) * 111.2 * math.cos(math.radians((minlat + maxlat) / 2))
    return max(ns, ew)


def split_items(items, max_span_km):
    """items sorted by time. Start a new chunk when adding the next aircraft would push the chunk's
    north-south or east-west extent past max_span_km, or past MAX_PER_FILE aircraft."""
    chunks, cur, box = [], [], None
    for it in items:
        lat, lon = it[3], it[4]
        has = bool(lat or lon)
        if has:
            nb = (lat, lat, lon, lon) if box is None else (min(box[0], lat), max(box[1], lat), min(box[2], lon), max(box[3], lon))
        if cur and ((has and extent_km(*nb) > max_span_km) or len(cur) >= MAX_PER_FILE):
            chunks.append(cur)
            cur, box = [], None
            nb = (lat, lat, lon, lon) if has else None
        cur.append(it)
        if has:
            box = nb
    if cur:
        chunks.append(cur)
    return chunks


def chunk_extent(chunk):
    pts = [(i[3], i[4]) for i in chunk if i[3] or i[4]]
    if not pts:
        return 0
    la = [p[0] for p in pts]
    lo = [p[1] for p in pts]
    return extent_km(min(la), max(la), min(lo), max(lo))


def body(items, day=None, extra=None):
    """items: (ts, icao, callsign, lat, lon). With day and extra given, each aircraft also carries the altitude,
    ground speed and heading Biscuit logged at that first sighting (WDGoWars holds a batch where every aircraft
    reports zero altitude, speed and heading)."""
    now = max(i[0] for i in items)
    ac = []
    for ts, icao, cs, lat, lon in sorted(items):
        a = {"hex": icao}
        if cs:
            a["flight"] = cs
        if lat or lon:
            a["lat"], a["lon"] = round(lat, 6), round(lon, 6)
            a["seen_pos"] = now - ts
        ex = (extra or {}).get((day, icao), {})
        if ex.get("alt") is not None:
            a["alt_baro"] = int(round(ex["alt"]))
        if ex.get("gs") is not None:
            a["gs"] = round(ex["gs"], 1)
        if ex.get("track") is not None:
            a["track"] = round(ex["track"], 1)
        if ex.get("rssi") is not None:
            a["rssi"] = ex["rssi"]
        a["seen"] = now - ts
        ac.append(a)
    return {"now": now, "messages": 0, "aircraft": ac}


def history_body(day, chunk, extra):
    """The shape of the old uConsole files (aircraft_YYYY-MM-DD.json): now, date, unique_aircraft_count, and
    first_seen / last_seen per aircraft. Fields Biscuit does not record are null."""
    ac = []
    last_all = 0
    for ts, icao, cs, lat, lon in sorted(chunk):
        ex = extra.get((day, icao), {})
        last = ex.get("last", ts)
        last_all = max(last_all, last)
        has = bool(lat or lon)
        ac.append({"hex": icao, "flight": cs or None,
                   "lat": round(lat, 6) if has else None, "lon": round(lon, 6) if has else None,
                   "alt_baro": ex.get("alt"), "alt_geom": None, "gs": ex.get("gs"), "track": ex.get("track"),
                   "baro_rate": None, "squawk": None, "category": None, "rssi": ex.get("rssi"),
                   "messages": ex.get("n", 1), "first_seen": ts, "last_seen": last})
    return {"now": last_all, "date": day, "unique_aircraft_count": len(ac), "aircraft": ac}


def post(key, data):
    req = Request(URL, data=data, method="POST")
    req.add_header("Content-Type", "application/gzip")
    req.add_header("X-File-Name", "aircraft.json.gz")
    req.add_header("X-API-Key", key)
    req.add_header("User-Agent", "biscuit-to-wardrift/1.0")
    req.add_header("Accept", "application/json")
    with urlopen(req, timeout=90) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out-dir", default=os.path.expanduser("~/Desktop/wardrift_json"))
    ap.add_argument("--upload", action="store_true", help="also POST each day to Wardrift")
    ap.add_argument("--key-file", default=os.path.expanduser("~/wardrift.key"))
    ap.add_argument("--max-age-hours", type=float, default=72.0)
    ap.add_argument("--max-span-km", type=float, default=600.0,
                    help="largest north-south or east-west extent of one file (lower it if the game still rejects a file)")
    a = ap.parse_args()

    first, extra, rows, bad_icao, bad_time = load(a.csv)
    days = {}
    for (day, icao), (ts, cs, lat, lon) in first.items():
        days.setdefault(day, []).append((ts, icao, cs, lat, lon))

    os.makedirs(a.out_dir, exist_ok=True)
    hdir = os.path.join(a.out_dir, "history_style")
    os.makedirs(hdir, exist_ok=True)
    now = time.time()
    cutoff = now - a.max_age_hours * 3600
    print("CSV rows read:                 %d" % rows)
    if bad_icao or bad_time:
        print("Skipped (bad ICAO / bad time): %d / %d" % (bad_icao, bad_time))
    print("Aircraft-days (one per UTC day): %d" % len(first))
    print("Output folder: %s" % a.out_dir)

    key = None
    if a.upload:
        try:
            key = open(a.key_file).read().strip()
        except OSError:
            sys.exit("Cannot read the key file %s" % a.key_file)
        if len(key) < 8 or any(c.isspace() for c in key):
            sys.exit("The key file looks wrong (empty, too short, or contains spaces).")

    total_credited = 0
    for day in sorted(days):
        items = sorted(days[day])
        too_old = sum(1 for i in items if i[0] < cutoff)
        parts = split_items(items, a.max_span_km)
        for old in glob.glob(os.path.join(a.out_dir, "adsb_%s*.json*" % day)):
            os.remove(old)      # clear files from an earlier run so a stale unsplit file never gets uploaded
        for old in glob.glob(os.path.join(hdir, "adsb_%s*.json" % day)):
            os.remove(old)
        for p, chunk in enumerate(parts, 1):
            doc = body(chunk, day, extra)
            name = "adsb_%s%s" % (day, "" if len(parts) == 1 else "_part%d" % p)
            raw = (json.dumps(doc, separators=(",", ":")) + "\n").encode("utf-8")
            with open(os.path.join(a.out_dir, name + ".json"), "wb") as f:
                f.write(raw)
            gz = gzip.compress(raw[:-1], mtime=0)   # API body has no trailing newline
            with open(os.path.join(a.out_dir, name + ".json.gz"), "wb") as f:
                f.write(gz)
            with open(os.path.join(hdir, name + ".json"), "w") as f:
                json.dump(history_body(day, chunk, extra), f)
                f.write("\n")
            line = "  %s  %d aircraft, spans %d km" % (name, len(chunk), chunk_extent(chunk))
            if a.upload:
                fresh = [c for c in chunk if c[0] >= cutoff]
                if not fresh:
                    print(line + "  - all older than %g h, not uploaded" % a.max_age_hours)
                    continue
                gz = gzip.compress(json.dumps(body(fresh, day, extra), separators=(",", ":")).encode("utf-8"), mtime=0)
                try:
                    status, text = post(key, gz)
                except HTTPError as e:
                    msg = e.read().decode("utf-8", errors="replace")[:300].replace(key, "<KEY>")
                    print(line + "  UPLOAD FAILED HTTP %d: %s" % (e.code, msg))
                    sys.exit(1)
                except (URLError, OSError) as e:
                    print(line + "  UPLOAD FAILED: %s" % str(e).replace(key, "<KEY>"))
                    sys.exit(1)
                try:
                    res = json.loads(text)
                except ValueError:
                    res = {}
                cnt = int(res.get("adsb_count", 0) or 0)
                total_credited += cnt
                print(line + "  uploaded %d -> newly credited %d (%s%s)" % (
                    len(fresh), cnt, res.get("outcome", "?"), ", duplicate" if res.get("duplicate") else ""))
                time.sleep(1)
            else:
                note = "  (%d older than %g h; Wardrift would not credit them)" % (too_old, a.max_age_hours) if too_old else ""
                print(line + note)
    if a.upload:
        print("RESULT: newly credited aircraft = %d" % total_credited)
    else:
        print("Nothing was uploaded. Add --upload --key-file <file> to send.")


if __name__ == "__main__":
    main()
