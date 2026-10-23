"""Maintainer-run ``jwts`` commands for publishing HIT academic data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import __version__
from .checkpoint import CrawlCheckpoint
from .client import TeachingSystemClient
from .config import DEFAULT_DATA_DIR, DEFAULT_WORK_DIR, Settings
from .data_merge import merge_data, write_files
from .discovery import discover_plans
from .errors import (
    AuthenticationError,
    ConfigError,
    HoahrbJwtsError,
    ParseError,
    PublicationError,
    TransportError,
    ValidationError,
)
from .major_checkout import DEFAULT_MAJOR_DATA_DIR, sync_major_checkout
from .normalize import normalize_plan
from .writer import (
    GradePublicationSummary,
    PublicationSummary,
    publish_plans,
)


def _year(value: str) -> str:
    if not re.fullmatch(r"20\d{2}", value):
        raise argparse.ArgumentTypeError("year must match 20XX")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl teaching plans and account-scoped grade contributions, then merge "
            "them into major data while preserving existing grading. Commands never commit or push."
        ),
        epilog=(
            "Examples:\n"
            "  jwts crawl --years 2024 2025\n"
            "    Fetch plans and course introductions into local data; no Git synchronization.\n"
            "  jwts merge-data\n"
            "    Sync adjacent hoa-major-data and merge local output, preserving grades.\n"
            "  jwts grades\n"
            "    Sync adjacent hoa-major-data and contribute grades for the signed-in account.\n"
            "Use jwts COMMAND --help for defaults, safeguards and local-only examples."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"jwts {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    crawl_parser = subparsers.add_parser(
        "crawl",
        help="crawl teaching plans and generate course introductions",
        description=(
            "Crawl HIT execution teaching plans and generate or update "
            "course_introductions.json for the courses found in those plans. "
            "By default, write to src/hoahrb_jwts/data. No Git fetch, clone, commit or push. "
            "Only requested years are rebuilt; existing grade_details are preserved. "
            "Refuse publication if rebuilding would remove contributed grade_details."
        ),
        epilog=(
            "Examples:\n"
            "  jwts crawl --years 2024 2025\n"
            "  jwts merge-data\n"
            "    Fetch locally, then sync and merge into adjacent hoa-major-data.\n"
            "  jwts crawl --years 2025 --fresh\n"
            "    Discard this output/year checkpoint and fetch all responses again.\n"
            "  jwts crawl --years 2025 --data-dir ./export --no-filename-fallback\n"
            "    Fail on filename collisions instead of applying code suffixes.\n"
            "Rerun the same output/year command to resume after failure. Cached responses\n"
            "are reparsed with current rules; successful publication clears the checkpoint.\n"
            "Filename fallbacks are listed at completion; grades_summary.json is untouched."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    crawl_parser.add_argument(
        "--years",
        nargs="+",
        required=True,
        type=_year,
        help="one or more enrollment years in 20XX format; only these years are rebuilt",
    )
    crawl_parser.add_argument(
        "--no-filename-fallback",
        action="store_true",
        help="disable automatic code suffixes for filename collisions; fail before publication and keep the checkpoint",
    )
    crawl_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_WORK_DIR / ".cache" / "jwts",
        help="checkpoint root (default: .cache/jwts under the jwts project); entries are scoped by output directory and year set",
    )
    crawl_parser.add_argument(
        "--fresh",
        action="store_true",
        help="discard the checkpoint for this output/year set and refetch all responses; does not delete published data",
    )
    crawl_parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="output directory (default: src/hoahrb_jwts/data); no automatic Git synchronization even for a checkout",
    )
    crawl_parser.add_argument(
        "--relogin",
        action="store_true",
        help="open a browser to reauthenticate using saved session state and persist verified cookies",
    )
    crawl_parser.add_argument(
        "--benchmark", action="store_true", help="print total local crawl duration"
    )
    grades_parser = subparsers.add_parser(
        "grades",
        aliases=["crawl-grades"],
        help="crawl account-scoped grade contributions",
        description=(
            "Fetch only grade-component names and weights visible to the signed-in account. "
            "Identify its enrollment year and exact major code from live account pages. "
            "Save grade_contributions/<content-hash>.json, then merge into grade_details "
            "of that account's plan only. No personal scores, names or student IDs are exported. "
            "The alias crawl-grades has identical behavior; grades_summary.json is untouched."
        ),
        epilog=(
            "Examples:\n"
            "  jwts grades\n"
            "    Locate or clone hoa-major-data beside the jwts project, safely sync\n"
            "    origin/main, then fetch and merge the current account's contribution.\n"
            "  jwts crawl-grades --data-dir ../hoa-major-data --no-sync\n"
            "    Use an existing local checkout without Git synchronization.\n"
            "  jwts grades --data-dir ./export\n"
            "    Save to an explicit non-Git directory without cloning or fetching.\n"
            "A missing target plan stops merging but the contribution file is retained.\n"
            "Conflicting grades retain existing values; incoming contributions are kept.\n"
            "Unmatched courses are reported and retained in the contribution file.\n"
            "Incoming Git updates with local changes stop synchronization; nothing is discarded.\n"
            "Never commit or push automatically."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grades_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="output directory (default: automatically sync hoa-major-data beside the jwts project); explicit non-Git directories skip Git sync",
    )
    grades_parser.add_argument(
        "--relogin",
        action="store_true",
        help="open a browser to reauthenticate using saved session state and persist verified cookies",
    )
    grades_parser.add_argument(
        "--benchmark", action="store_true", help="print total local crawl duration"
    )
    merge_parser = subparsers.add_parser(
        "merge-data",
        help="merge local crawl output into an existing major-data checkout",
        description=(
            "Merge local plans, course introductions and grade contributions into major data. "
            "Preserve existing grade_details and plans absent from the source. "
            "By default, locate or clone hoa-major-data beside the jwts project, "
            "fetch origin/main and fast-forward when safe. Never commit or push."
        ),
        epilog=(
            "Examples:\n"
            "  jwts merge-data\n"
            "    Sync adjacent hoa-major-data, then merge the default local crawl output.\n"
            "  jwts merge-data --source ./export --data-dir ../hoa-major-data --no-sync\n"
            "    Merge entirely locally without fetching or cloning.\n"
            "Conflicting grades retain the target value and preserve incoming contributions.\n"
            "Incoming Git updates with local changes stop synchronization without discarding data."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    merge_parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="source directory containing plans/ and/or grade_contributions/ (default: bundled data directory)",
    )
    merge_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="target directory (default: hoa-major-data beside the jwts project); explicit non-Git targets are merged locally",
    )
    for command_parser in (grades_parser, merge_parser):
        command_parser.add_argument(
            "--no-sync",
            action="store_true",
            help="skip Git fetch/clone/fast-forward and use local target data; grades still contacts the teaching system",
        )
    return parser


def _project_dotenv_path() -> Path:
    return DEFAULT_WORK_DIR / ".env"


def _print_progress(message: str) -> None:
    print(f"progress: {message}", file=sys.stderr, flush=True)


def _major_data_target(args: argparse.Namespace) -> Path:
    target = Path(args.data_dir) if args.data_dir is not None else DEFAULT_MAJOR_DATA_DIR
    # An explicit directory remains a standalone output unless it is a checkout.
    if not getattr(args, "no_sync", False) and (
        args.data_dir is None or (target / ".git").exists()
    ):
        _print_progress(f"syncing major data: {target}")
        sync_major_checkout(target)
    return target


def run_crawl(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    dotenv_path: Path | None = None,
) -> PublicationSummary:
    years = tuple(sorted(set(args.years)))
    settings = Settings.from_sources(environ, dotenv_path or _project_dotenv_path())
    data_dir = Path(args.data_dir)
    checkpoint = CrawlCheckpoint(
        getattr(args, "cache_dir", DEFAULT_WORK_DIR / ".cache" / "jwts"),
        data_dir,
        years,
    )
    if getattr(args, "fresh", False):
        checkpoint.clear()
    gateway = TeachingSystemClient(settings, on_progress=_print_progress, checkpoint=checkpoint)
    if getattr(args, "relogin", False):
        gateway.relogin()
    else:
        gateway.refresh_cookie()
    discovered = discover_plans(gateway, years)
    normalized = tuple(normalize_plan(plan) for plan in discovered)
    course_codes = {course.course_code for plan in normalized for course in plan.courses}
    introductions = gateway.get_course_introductions(course_codes) if course_codes else {}
    summary = publish_plans(
        data_dir,
        normalized,
        set(years),
        introductions,
        allow_filename_fallback=not getattr(args, "no_filename_fallback", False),
    )
    checkpoint.clear()
    return summary


def run_grades(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    dotenv_path: Path | None = None,
) -> GradePublicationSummary:
    """Save account-scoped contributions before merging into the matching plan."""

    data_dir = _major_data_target(args)
    settings = Settings.from_sources(environ, dotenv_path or _project_dotenv_path())
    gateway = TeachingSystemClient(settings, on_progress=_print_progress)
    if getattr(args, "relogin", False):
        gateway.relogin()
    else:
        gateway.refresh_cookie()
    summary = gateway.get_grade_summary()
    profile = gateway.get_student_profile()
    bundle = {"profile": profile, "grades": summary}
    body = (json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(body).hexdigest()
    name = f"grade_contributions/{digest}.json"
    write_files(data_dir, {name: body})
    _print_progress(f"saved grade contribution: {name}")
    if not profile.get("major_code"):
        raise ValidationError(
            "account plan is ambiguous; contribution saved without modifying plans; "
            f"candidate codes: {profile.get('candidate_codes') or 'none'}"
        )
    # Merge a contribution-only directory; never regenerate unrelated plans.
    import tempfile

    with tempfile.TemporaryDirectory(prefix=".jwts-contribution-", dir=data_dir.parent) as folder:
        contribution_dir = Path(folder)
        write_files(contribution_dir, {name: body})
        result = merge_data(contribution_dir, data_dir)
    return GradePublicationSummary(len(summary), 0, result.updated, 0, result.conflicts)


def _exit_code(error: HoahrbJwtsError) -> int:
    if isinstance(error, (ConfigError, AuthenticationError)):
        return 2
    if isinstance(error, (TransportError, ParseError)):
        return 3
    if isinstance(error, (ValidationError, PublicationError)):
        return 4
    return 4


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "merge-data":
        try:
            summary = merge_data(args.source, _major_data_target(args))
        except (HoahrbJwtsError, OSError, ValueError, KeyError, TypeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 4
        print(
            f"merged plans: added={summary.added} updated={summary.updated} unchanged={summary.unchanged}"
        )
        for identity in summary.conflicts:
            print(
                f"grade conflict (existing retained; incoming contribution preserved): {identity}"
            )
        return 0
    from .diagnostics import configure_auth_logging, logger

    try:
        configure_auth_logging()
    except OSError:
        print("warning: unable to create authentication diagnostic log", file=sys.stderr)
    started_at = time.perf_counter()
    try:
        if args.command == "crawl":
            summary = run_crawl(args, environ)
        else:
            summary = run_grades(args, environ)
    except HoahrbJwtsError as error:
        logger.warning("command_failed type=%s", type(error).__name__)
        print(f"error: {error}", file=sys.stderr)
        return _exit_code(error)
    if args.command == "crawl":
        print(
            "published plans: "
            f"added={summary.added} updated={summary.updated} "
            f"removed={summary.removed} unchanged={summary.unchanged}"
        )
        if summary.introductions is not None:
            introductions = summary.introductions
            print(
                "published introductions: "
                f"courses={introductions.courses} added={introductions.added} "
                f"updated={introductions.updated} unchanged={introductions.unchanged}"
            )
    else:
        print(
            "published grades: "
            f"courses={summary.courses} added={summary.added} "
            f"updated={summary.updated} unchanged={summary.unchanged}"
        )
        print(f"grade conflicts (existing retained): variants={len(summary.conflicts)}")
        for identity in summary.conflicts:
            print(f"  {identity}")
    if args.benchmark:
        print(f"benchmark: crawl_seconds={time.perf_counter() - started_at:.3f}")
    if args.command == "crawl":
        print(f"filename fallbacks: files={len(summary.filename_fallbacks)}")
        for filename in summary.filename_fallbacks:
            print(f"  {filename}")
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
