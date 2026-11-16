# Changelog

## Unreleased

- Import: upload FIT, GPX and TCX files to Garmin Connect, chosen one by one or as a whole folder, also compressed `.gz` files from a Strava export.
- Files are checked before sending; activities already in Garmin Connect and files uploaded before are skipped, so running the same import again sends nothing twice.
- Imported activities are not passed on by Garmin to Strava or other connected apps.
- Command line: `fitbatch upload <files or folders>`.

## 0.1.0 beta 1 (2026-11-16)

First test version for Windows and macOS.

- Download activities from Garmin Connect as FIT, GPX, TCX, KML and lap splits CSV.
- Filters by date range, activity type and words in the activity name.
- Files already in the target folder are skipped.
- Login session stored encrypted: Windows DPAPI on Windows, Keychain on macOS.
