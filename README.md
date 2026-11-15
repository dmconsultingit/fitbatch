# FitBatch

**Bulk download & upload of your training data. For Garmin Connect.**

FitBatch is a free, open-source, portable desktop app for amateur athletes, coaches and anyone who wants their raw training files on their own disk: for Excel, Python, GoldenCheetah or AI tools.

> **Status:** early development (0.0.1). Nothing works yet. Watch the repository to follow progress.

## Planned features

- Bulk download of activities from Garmin Connect (.FIT, GPX, TCX)
- Upload of workouts and training plans back to Garmin Connect
- Portable: no installation required (Windows first, then Linux and macOS)
- Graphical interface, plus a command-line mode for technical users

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
