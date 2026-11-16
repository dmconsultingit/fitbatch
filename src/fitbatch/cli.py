"""Command-line interface for FitBatch."""

from __future__ import annotations

import argparse
import getpass
import logging
from collections.abc import Sequence
from datetime import date, datetime, time
from pathlib import Path

from fitbatch import __version__
from fitbatch.storage.paths import default_paths

log = logging.getLogger(__name__)

FORMAT_CHOICES = ["fit", "tcx", "gpx", "kml", "csv"]
EARLIEST = date(1990, 1, 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fitbatch",
        description="Bulk download & upload of your training data. For Garmin Connect.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.add_parser("gui", help="open the graphical interface (default)")
    sub.add_parser("info", help="show where FitBatch keeps its data")
    sub.add_parser("login", help="log in to Garmin Connect with email and password")
    sub.add_parser("logout", help="delete the saved Garmin Connect session from this computer")

    export = sub.add_parser("export", help="download activities from Garmin Connect")
    when = export.add_mutually_exclusive_group(required=True)
    when.add_argument("--from", dest="date_from", type=date.fromisoformat, metavar="YYYY-MM-DD")
    when.add_argument("--all", action="store_true", help="the whole history")
    export.add_argument("--to", dest="date_to", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="last day, inclusive (default: today)")  # fmt: skip
    export.add_argument("--formats", nargs="+", choices=FORMAT_CHOICES, default=["fit"],
                        help="file formats (default: fit); csv = splits")  # fmt: skip
    export.add_argument("--out", type=Path, help="target folder (default: from settings)")
    export.add_argument("--name", nargs="+", default=[], metavar="PHRASE",
                        help="name contains any of these whole words/phrases")  # fmt: skip
    export.add_argument("--exclude", nargs="+", default=[], metavar="PHRASE",
                        help="skip names containing any of these")  # fmt: skip
    export.add_argument("--type", dest="types", nargs="+", default=[], metavar="TYPE",
                        help="type keys, e.g. running cycling strength_training")  # fmt: skip
    export.add_argument("--overwrite", action="store_true", help="download again and overwrite")
    export.add_argument("--delay", type=float, default=1.0, help="seconds between downloads")
    export.add_argument("--list-only", action="store_true", help="only list, do not download")

    upload = sub.add_parser("upload", help="upload FIT, GPX or TCX files to Garmin Connect")
    upload.add_argument("paths", nargs="+", type=Path, metavar="PATH",
                        help="files or folders (.fit, .gpx, .tcx, also .gz)")  # fmt: skip
    upload.add_argument("--no-subfolders", action="store_true",
                        help="in folders, ignore subfolders")  # fmt: skip
    upload.add_argument("--delay", type=float, default=2.0, help="seconds between uploads")
    upload.add_argument("--list-only", action="store_true", help="only check, do not upload")
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


def _services():  # noqa: ANN202 - imported lazily to keep `--version`/`info` fast
    from fitbatch.logging_setup import setup_logging
    from fitbatch.services import create_services

    services = create_services()
    setup_logging(services.paths.logs_dir)
    return services


def _login() -> int:
    from fitbatch.adapters.base import AdapterError
    from fitbatch.core.login_guard import LoginLockedError

    services = _services()
    print("Log in to Garmin Connect. Your password is used only for this login and never saved.")
    email = input("Email: ").strip()
    password = getpass.getpass("Password (hidden): ")
    try:
        adapter = services.login_garmin_email(
            email, password, mfa_provider=lambda: input("Garmin MFA code: ").strip()
        )
    except LoginLockedError as exc:
        print(f"Too many attempts. Try again in {int(exc.remaining_s // 60) + 1} min.")
        return 1
    except AdapterError as exc:
        print(f"Login failed: {exc}")
        return 1
    finally:
        del password
    print(f"Logged in as {adapter.account_label}. Session saved (encrypted) on this computer.")
    return 0


def _logout() -> int:
    _services().garmin_email.logout()
    print("Saved Garmin Connect session deleted from this computer.")
    return 0


def _export(args: argparse.Namespace) -> int:
    from fitbatch.core.export import (
        Exporter,
        ExportEvent,
        ExportRequest,
        FileFailed,
        FileSaved,
        FileSkipped,
        Progress,
        Waiting,
    )
    from fitbatch.core.filters import ActivityFilter
    from fitbatch.core.models import ExportFormat

    services = _services()
    adapter = services.connect_saved_garmin_email()
    if adapter is None:
        print("Not logged in (or the session expired). Run: fitbatch login")
        return 1

    first = EARLIEST if args.all else args.date_from
    last = args.date_to or date.today()
    # F6/D8: whole days in this computer's time zone.
    start = datetime.combine(first, time.min).astimezone()
    end = datetime.combine(last, time.max).astimezone()
    formats = [ExportFormat.SPLITS_CSV if f == "csv" else ExportFormat(f) for f in args.formats]
    out_dir = args.out or Path(services.load_settings().download_dir)
    request = ExportRequest(
        start=start,
        end=end,
        formats=formats,
        out_dir=out_dir.expanduser(),
        activity_filter=ActivityFilter(args.name, args.exclude, args.types),
        overwrite=args.overwrite,
    )

    def show(event: ExportEvent) -> None:
        match event:
            case Progress(done, total, activity):
                when = activity.start_time.astimezone().strftime("%Y-%m-%d %H:%M")
                print(f"[{done}/{total}] {when} {activity.type_key}: {activity.name}")
            case FileSaved(_, fmt, path, substitute):
                note = " (no FIT on Garmin, original upload saved)" if substitute else ""
                print(f"    {fmt.value}: saved {path.name}{note}")
            case FileSkipped(_, fmt, reason):
                print(f"    {fmt.value}: skipped ({reason.replace('_', ' ')})")
            case FileFailed(_, fmt, message):
                print(f"    {fmt.value}: ERROR {message}")
            case Waiting(seconds, _):
                print(f"    Garmin is limiting requests, waiting {int(seconds)} s ...")

    with services.open_registry() as registry:
        exporter = Exporter(adapter, registry, on_event=show, delay_s=args.delay)
        print(f"Listing activities {first} – {last} ...")
        activities = exporter.plan(request)
        print(f"{len(activities)} activities match. Target folder: {request.out_dir}")
        if args.list_only or not activities:
            return 0
        try:
            summary = exporter.run(request, activities)
        except KeyboardInterrupt:
            print("\nCancelled. Run the same command again to resume.")
            return 130
        finally:
            adapter.persist_session()

    print(
        f"\nDone: saved {summary.saved}, skipped {summary.skipped}, errors {summary.failed}."
        f" Folder: {summary.out_dir}"
    )
    if summary.paused_reason == "rate_limit":
        print("Paused: Garmin is limiting requests. Wait 15+ minutes and run the same command.")
        return 3
    if summary.paused_reason == "authentication":
        print("Paused: the session is no longer valid. Run: fitbatch login")
        return 3
    return 0 if summary.failed == 0 else 2


def _upload(args: argparse.Namespace) -> int:
    from fitbatch.core.export import Waiting
    from fitbatch.core.upload import (
        Uploaded,
        Uploader,
        UploadEvent,
        UploadFailed,
        UploadProgress,
        UploadSkipped,
        scan_upload_paths,
    )

    missing = [str(p) for p in args.paths if not p.exists()]
    if missing:
        print(f"Not found: {', '.join(missing)}")
        return 1
    files = scan_upload_paths(args.paths, recursive=not args.no_subfolders)
    problems = [f for f in files if f.problem]
    print(f"{len(files)} files found, {len(problems)} cannot be uploaded.")
    for f in problems:
        print(f"    {f.path}: {f.problem.replace('_', ' ')}")

    services = _services()
    adapter = services.connect_saved_garmin_email()
    if adapter is None:
        print("Not logged in (or the session expired). Run: fitbatch login")
        return 1

    def show(event: UploadEvent | Waiting) -> None:
        match event:
            case UploadProgress(done, total, f):
                print(f"[{done}/{total}] {f.path}")
            case Uploaded():
                print("    uploaded")
            case UploadSkipped(_, reason):
                print(f"    skipped ({reason.replace('_', ' ')})")
            case UploadFailed(_, message):
                print(f"    ERROR {message}")
            case Waiting(seconds, _):
                print(f"    Garmin is limiting requests, waiting {int(seconds)} s ...")

    with services.open_upload_registry() as registry:
        uploader = Uploader(adapter, registry, on_event=show, delay_s=args.delay)
        pending = uploader.pending(files)
        print(f"{len(pending)} new files to upload to {adapter.account_label}.")
        if args.list_only or not pending:
            return 0
        try:
            summary = uploader.run(files)
        except KeyboardInterrupt:
            print("\nCancelled. Run the same command again to continue.")
            return 130
        finally:
            adapter.persist_session()

    print(
        f"\nDone: uploaded {summary.uploaded}, already in Garmin Connect {summary.duplicates},"
        f" skipped {summary.skipped}, errors {summary.failed}."
    )
    if summary.paused_reason == "rate_limit":
        print("Paused: Garmin is limiting requests. Wait 15+ minutes and run the same command.")
        return 3
    if summary.paused_reason == "authentication":
        print("Paused: the session is no longer valid. Run: fitbatch login")
        return 3
    return 0 if summary.failed == 0 else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    match args.command:
        case "info":
            return _info()
        case "login":
            return _login()
        case "logout":
            return _logout()
        case "export":
            return _export(args)
        case "upload":
            return _upload(args)

    # Default: graphical interface. Imported lazily so other commands start without loading Flet.
    from fitbatch.ui.app import main as gui_main

    gui_main()
    return 0
