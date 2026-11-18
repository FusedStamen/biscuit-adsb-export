# Pulling ADS-B aircraft out of a Biscuit backup

Biscuit does not export its aircraft log (the wardrive CSVs are Wi-Fi/BLE only). The aircraft ARE stored
in the app's backup file, which is a single SQLite database. These two scripts read it and build upload files.

Files (keep both in your Downloads folder on the Mac):
- biscuit_adsb_export.sh    reads the backup, writes the aircraft to a CSV (read-only, changes nothing)
- biscuit_to_wardrift.py    turns that CSV into upload JSON, one set per UTC day (split by area when you traveled)

## Steps

1. Make a backup in the Biscuit app and save it to your Mac (Downloads). It is named like
   Biscuit-Backup-YYYY-MM-DD-HHMM.biscuitbackup  (about 260 MB for a few weeks of drives).

2. Export the aircraft. In Terminal on the Mac:

       bash ~/Downloads/biscuit_adsb_export.sh ~/Downloads/Biscuit-Backup-YYYY-MM-DD-HHMM.biscuitbackup

   Tip: type everything up to the last space, then drag the backup file from Finder onto the Terminal window.
   Output (on your Desktop):
       biscuit_adsb.csv            one row per aircraft observation (ICAO, position, altitude, speed, heading, signal)
       biscuit_adsb_sessions.txt   one line per Biscuit drive: when it ran, aircraft count, weakest/strongest signal
   Only the aircraft table is read. Wi-Fi and Bluetooth data are not exported.

3. Convert to upload files:

       python3 ~/Downloads/biscuit_to_wardrift.py ~/Desktop/biscuit_adsb.csv

   Output: a folder named wardrift_json on your Desktop:
       adsb_YYYY-MM-DD.json            one per UTC day (adsb_..._part1.json, _part2.json ... when you traveled)
       adsb_YYYY-MM-DD.json.gz         the same, gzipped (the exact body Wardrift's API takes)
       history_style/                  the same files in the older uConsole layout (only if the site wants it)
   Nothing is sent anywhere unless you add --upload.

4. Upload.
   - WDGoWars: upload the .json files (not history_style) on the uplink page. It rejects a file whose aircraft
     span too large an area for one receiver, so the converter splits travel days automatically (default 600 km;
     add --max-span-km 400 if a file is still rejected).
   - Wardrift: it took the files on its website. It credits one aircraft per UTC day and only observations under
     72 hours old, so upload soon after a trip. Optional direct upload with your API key saved as plain text in
     ~/wardrift.key:
         python3 ~/Downloads/biscuit_to_wardrift.py ~/Desktop/biscuit_adsb.csv --upload

## Getting results back to Claude
Attach biscuit_adsb.csv and biscuit_adsb_sessions.txt to the chat (not the 260 MB backup). They contain aircraft
only (public data), no wardrive locations of yours.

## What the files contain, and why
- Every aircraft heard is included, nothing filtered. One entry per aircraft per UTC day (its first sighting).
- Each aircraft carries altitude, ground speed and heading. WDGoWars holds a batch where every aircraft
  reports zero for all three, so do not strip those fields.
- Aircraft the Biscuit heard without a position are not in the backup (every row needs a position).

## If something goes wrong
- "Database not found": check the backup path and file name (use quotes if the name has spaces).
- "sqlite3 not found": it is built into macOS; if missing, install Xcode command line tools.
- python3 asks to install developer tools: accept, or run it from a Mac that already has them.
- WDGoWars "REJECTED ... geographic span": lower --max-span-km and rerun.
- WDGoWars "held": the card's details show the reason; send it to Loco (admin) if it is not obvious.
- The export script overwrites the previous biscuit_adsb.csv and biscuit_adsb_sessions.txt on the Desktop.
  Rename them first if you want to keep them. The converter clears old files for the same day in wardrift_json.
