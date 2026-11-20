#!/usr/bin/env python3
"""Export ONLY the ADS-B aircraft rows from a Biscuit backup (read-only).

Works on Windows, Mac and Linux. Needs only Python 3 (sqlite3 is built in).

Usage:
    python biscuit_adsb_export.py "C:\\Users\\you\\Downloads\\Biscuit-Backup-....biscuitbackup"
    python biscuit_adsb_export.py backup.biscuitbackup --out-dir "C:\\Users\\you\\Desktop"

Outputs (default: your Desktop, or the current folder if there is no Desktop):
    biscuit_adsb.csv            one row per aircraft observation
    biscuit_adsb_sessions.txt   per-session summary (which session = which drive)
Wi-Fi and Bluetooth tables are NOT exported.
"""
import argparse, csv, os, sqlite3, sys
from pathlib import Path

COLS = """a.session_id, a.sequence, a.icao, a.timestamp,
  a.latitude AS lat, a.longitude AS lon,
  json_extract(a.payload,'$.altitudeFeet')            AS alt_ft,
  json_extract(a.payload,'$.rssiDBm')                 AS rssi_dbm,
  json_extract(a.payload,'$.callsign')                AS callsign,
  json_extract(a.payload,'$.speedKnots')              AS speed_kt,
  json_extract(a.payload,'$.bearingDegrees')          AS bearing_deg,
  json_extract(a.payload,'$.seenAgeMilliseconds')     AS seen_age_ms,
  json_extract(a.payload,'$.positionAgeMilliseconds') AS pos_age_ms,
  json_extract(a.payload,'$.nodeMAC')                 AS node_mac"""

SESSIONS = """SELECT s.id AS session_id, s.mode, s.start_time, s.end_time, s.visible,
  count(a.row_id) AS adsb_rows, count(DISTINCT a.icao) AS unique_icao,
  min(a.timestamp) AS first_ts, max(a.timestamp) AS last_ts,
  min(json_extract(a.payload,'$.rssiDBm')) AS weakest_rssi,
  max(json_extract(a.payload,'$.rssiDBm')) AS strongest_rssi,
  group_concat(DISTINCT json_extract(a.payload,'$.nodeMAC')) AS node_macs
FROM sessions s LEFT JOIN adsb_observations a ON a.session_id = s.id
GROUP BY s.id ORDER BY s.start_time"""


def default_out():
    home = Path.home()
    for p in (home / "Desktop", home / "OneDrive" / "Desktop"):
        if p.is_dir():
            return p
    return Path.cwd()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", help="path to the .biscuitbackup file")
    ap.add_argument("--out-dir", default=None, help="where to write the two files")
    a = ap.parse_args()

    db = Path(a.backup)
    if not db.is_file():
        sys.exit(f"Database not found: {db}")
    out = Path(a.out_dir) if a.out_dir else default_out()
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "biscuit_adsb.csv"
    sum_path = out / "biscuit_adsb_sessions.txt"

    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        cur = con.execute(f"SELECT {COLS} FROM adsb_observations a "
                          "ORDER BY a.session_id, a.sequence, a.row_id")
        n = 0
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([d[0] for d in cur.description])
            for row in cur:
                w.writerow(["" if v is None else v for v in row])
                n += 1

        cur = con.execute(SESSIONS)
        head = [d[0] for d in cur.description]
        rows = cur.fetchall()
        total = con.execute("SELECT count(*), count(DISTINCT icao) FROM adsb_observations").fetchone()
        with open(sum_path, "w", encoding="utf-8") as f:
            f.write("=== ADS-B ROWS PER SESSION ===\n")
            f.write("(start_time and end_time are raw values from the sessions table)\n")
            f.write("|".join(head) + "\n")
            for r in rows:
                f.write("|".join("" if v is None else str(v) for v in r) + "\n")
            f.write("\n=== TOTAL ADS-B ROWS / UNIQUE ICAO ===\n")
            f.write(f"{total[0]}|{total[1]}\n")
    finally:
        con.close()

    print("Wrote:")
    print(f"  {csv_path}  ({n} rows)")
    print(f"  {sum_path}")


if __name__ == "__main__":
    main()
