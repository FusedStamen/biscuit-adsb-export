#!/bin/bash
# Export ONLY the ADS-B aircraft rows from a Biscuit backup (read-only).
# Run on the Mac:  bash ~/Downloads/biscuit_adsb_export.sh
# Outputs (on Desktop):
#   biscuit_adsb.csv            - one row per aircraft observation (aircraft data only)
#   biscuit_adsb_sessions.txt   - per-session summary (which session = which drive/test)
# Attach both files to Claude. Wi-Fi/Bluetooth tables are NOT exported.
# Optional: bash biscuit_adsb_export.sh /path/to/other.biscuitbackup

DB="${1:-$HOME/Downloads/Biscuit-Backup-2026-11-17-2114.biscuitbackup}"
CSV="$HOME/Desktop/biscuit_adsb.csv"
SUM="$HOME/Desktop/biscuit_adsb_sessions.txt"

if [ ! -f "$DB" ]; then echo "Database not found: $DB"; exit 1; fi
if ! command -v sqlite3 >/dev/null 2>&1; then echo "sqlite3 not found"; exit 1; fi

sqlite3 -readonly "$DB" -csv -header "
SELECT
  a.session_id,
  a.sequence,
  a.icao,
  a.timestamp,
  a.latitude  AS lat,
  a.longitude AS lon,
  json_extract(a.payload,'\$.altitudeFeet')              AS alt_ft,
  json_extract(a.payload,'\$.rssiDBm')                   AS rssi_dbm,
  json_extract(a.payload,'\$.callsign')                  AS callsign,
  json_extract(a.payload,'\$.speedKnots')                AS speed_kt,
  json_extract(a.payload,'\$.bearingDegrees')            AS bearing_deg,
  json_extract(a.payload,'\$.seenAgeMilliseconds')       AS seen_age_ms,
  json_extract(a.payload,'\$.positionAgeMilliseconds')   AS pos_age_ms,
  json_extract(a.payload,'\$.nodeMAC')                   AS node_mac
FROM adsb_observations a
ORDER BY a.session_id, a.sequence, a.row_id;
" > "$CSV" 2>&1

{
  echo "=== ADS-B ROWS PER SESSION ==="
  echo "(start_time and end_time are raw values from the sessions table)"
  sqlite3 -readonly "$DB" -header "
  SELECT
    s.id AS session_id,
    s.mode,
    s.start_time,
    s.end_time,
    s.visible,
    count(a.row_id)                        AS adsb_rows,
    count(DISTINCT a.icao)                 AS unique_icao,
    min(a.timestamp)                       AS first_ts,
    max(a.timestamp)                       AS last_ts,
    min(json_extract(a.payload,'\$.rssiDBm')) AS weakest_rssi,
    max(json_extract(a.payload,'\$.rssiDBm')) AS strongest_rssi,
    group_concat(DISTINCT json_extract(a.payload,'\$.nodeMAC')) AS node_macs
  FROM sessions s LEFT JOIN adsb_observations a ON a.session_id = s.id
  GROUP BY s.id
  ORDER BY s.start_time;
  " 2>&1
  echo
  echo "=== TOTAL ADS-B ROWS / UNIQUE ICAO ==="
  sqlite3 -readonly "$DB" "SELECT count(*), count(DISTINCT icao) FROM adsb_observations;" 2>&1
} > "$SUM" 2>&1

echo "Wrote:"
echo "  $CSV"
echo "  $SUM"
wc -l "$CSV"
