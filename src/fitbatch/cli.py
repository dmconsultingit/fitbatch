"""Command-line interface for FitBatch."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from fitbatch import __version__
from fitbatch.storage.paths import default_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fitbatch",
        description="Bulk download & upload of your training data. For Garmin Connect.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subcommands = parser.add_subparsers(dest="command", metavar="<command>")
    subcommands.add_parser("gui", help="open the graphical interface (default)")
    subcommands.add_parser("info", help="show where FitBatch keeps its data")

    return parser


def _info() -> int:
    paths = default_paths()
    print(f"FitBatch {__version__}")
    print(f"Data folder: {paths.root}")
    print(f"  settings:  {paths.settings_file}")
    print(f"  registry:  {paths.registry_file}")
    print(f"  secrets:   {paths.secrets_dir}")
    print(f"  logs:      {paths.logs_dir}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "info":
        return _info()

    # Default: graphical interface. Imported lazily so other commands start without loading Flet.
    from fitbatch.ui.app import main as gui_main

    gui_main()
    return 0
