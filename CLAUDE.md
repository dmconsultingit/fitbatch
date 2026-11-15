# FitBatch – project guide

FitBatch is a free, open-source (GPL-3.0-or-later) desktop app for bulk download and, later,
upload of training data from Garmin Connect. Python + Flet, Windows 10/11 first.
Requirement IDs (F1…, N1…, D1…) refer to the project requirements document.
`docs/requirements.md` is a local-only copy: it is excluded from git and must never be committed.

## Commands

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"   # set up / refresh the environment
.venv\Scripts\python -m pytest -q                  # tests
.venv\Scripts\python -m ruff format src tests      # format
.venv\Scripts\python -m ruff check src tests       # lint (must pass)
.venv\Scripts\fitbatch                             # run the GUI
.venv\Scripts\fitbatch info                        # show data folder locations
```

## Architecture (N15) – layers and allowed imports

```
ui/        Flet interface           → may import core, storage.paths
core/      logic: export, filters,  → may import adapters (interface only), storage
           registry, settings, conversions
adapters/  one per platform + access method, behind PlatformAdapter (N11)
storage/   data folder paths, atomic writes, encrypted secrets
```

- The UI never calls a platform or an adapter's network code directly; it goes through `core`.
- `core` depends on the `PlatformAdapter` protocol, never on a concrete adapter.
- Adapters declare `capabilities` and `remote_formats`; the UI enables/greys out features from
  them. Adding a platform must not require changes in export, filters or the registry.
- Logic must be testable without a window and without internet: use `adapters.fake.FakeAdapter`.

## Code standard (N16)

- PEP 8 enforced by ruff (config in `pyproject.toml`); `ruff check` must pass before a commit.
- Type hints everywhere in `core`, `adapters` and `storage`.
- Names and comments in code in English; user-facing messages PL/EN.
- Every feature in `core`, filters, registry and conversions comes with pytest tests.
- Datetimes are timezone-aware; convert to the computer's local time only for display and
  date-range filtering (D8).

## Security rules (N2) – non-negotiable

- Never write a password to disk, logs, error messages or exceptions. Keep it in memory only for
  the login call.
- Tokens and API keys go only through `storage.secrets` (DPAPI on Windows). Never in settings,
  logs or the repository.
- All logging goes through `logging_setup.setup_logging`, which redacts credential-like values.
  Do not log request/response bodies of login calls.
- On HTTP 429 raise `RateLimitError`; never retry in a tight loop. Limit login attempts.
- Pin library versions and check them for known vulnerabilities before upgrading.

## Data locations (D13)

Settings, registry, secrets and logs live in the user profile: `%APPDATA%\FitBatch`
(`storage.paths.default_paths`). Tests must use the `FITBATCH_DATA_DIR` override (already set
automatically by `tests/conftest.py`) and never touch the real folder.

## Packaging notes

- Trial build: `flet pack packaging/launcher.py -n FitBatch` → single ~70 MB `FitBatch.exe`.
- Known Flet 1.1.2 bug: `flet_cli/__pyinstaller/win_utils.py` opens the exe with
  `open(exe_path, "ab", encoding="utf-8")`, which always fails. Locally patched to
  `open(exe_path, "ab")` in `.venv`; re-apply after reinstalling flet-cli until fixed upstream.
- The packed exe extracts the Flet desktop client (~100 MB) to `%USERPROFILE%\.flet\client` on
  first start.
