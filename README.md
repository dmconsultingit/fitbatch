<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/fitbatch-horizontal-on-dark.svg">
    <img src="brand/fitbatch-horizontal-on-light.svg" alt="FitBatch" width="360">
  </picture>
</p>

# FitBatch

**Bulk download & upload of your training data. For Garmin Connect.**

FitBatch is a free, open-source, portable desktop app for amateur athletes, coaches and anyone who wants their raw training files on their own disk: for Excel, Python, GoldenCheetah or AI tools.

> **Status:** first test version (0.1.0 beta 1). Bulk download from Garmin Connect works; upload is planned.

## Features

- Bulk download of activities from Garmin Connect (original .FIT, GPX, TCX, KML, splits as CSV), filtered by date and activity type
- Already downloaded files are skipped, so you can run it again to fetch only new activities
- Portable: no installation, one file for Windows and an app for macOS (Apple Silicon and Intel)
- Graphical interface, plus a command-line mode for technical users
- Planned: upload of workouts and training plans back to Garmin Connect

## Download

Get the latest version from [Releases](https://github.com/dmconsultingit/fitbatch/releases):

- **Windows 10/11:** `FitBatch.exe`. If SmartScreen warns you, click *More info → Run anyway*.
- **macOS:** `FitBatch-macOS-arm64.zip` (Apple Silicon, M1 and newer) or `FitBatch-macOS-intel.zip`. Unzip, move FitBatch to Applications and open it. The first time, macOS blocks the app because it is not signed by an Apple developer yet: go to *System Settings → Privacy & Security* and click *Open Anyway*.

## Installation (development)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -e ".[dev]"
```

## Usage

```bash
fitbatch            # opens the graphical interface
fitbatch --version
```

## Disclaimer

FitBatch is an independent project. It is not affiliated with, endorsed by or sponsored by Garmin Ltd. or its subsidiaries. Garmin and Garmin Connect are trademarks of Garmin Ltd. or its subsidiaries.

## License

Copyright © 2026 [Dawid Medyński](https://dm-consulting-it.com)

FitBatch is licensed under the [GNU General Public License v3.0 or later](LICENSE).

Official site: [fitbatch.app](https://fitbatch.app)
