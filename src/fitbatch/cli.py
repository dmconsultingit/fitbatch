"""Command-line interface for FitBatch."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from fitbatch import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fitbatch",
        description="Bulk download & upload of your training data. For Garmin Connect.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subcommands = parser.add_subparsers(dest="command", metavar="<command>")
    subcommands.add_parser("gui", help="open the graphical interface (default)")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in (None, "gui"):
        # Imported lazily so that CLI-only commands start fast without loading Flet.
        from fitbatch.gui import main as gui_main

        gui_main()
        return 0

    return 0
