"""Maintainer-run ``jwts`` commands for publishing HIT academic data."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .checkpoint import CrawlCheckpoint
from .client import TeachingSystemClient
from .config import DEFAULT_DATA_DIR, Settings
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
from .normalize import normalize_plan
from .writer import (
    GradePublicationSummary,
    PublicationSummary,
    publish_grade_summary,
    publish_plans,
)


def _year(value: str) -> str:
    if not re.fullmatch(r"20\d{2}", value):
        raise argparse.ArgumentTypeError("year must match 20XX")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="JWTS academic data crawler",
        epilog=(
            "Examples:\n"
            "  jwts crawl --years 2024 2025 --data-dir ../hoa-major-data\n"
            "    Crawl plans and generate course_introductions.json.\n"
            "  jwts grades --data-dir ../hoa-major-data\n"
            "    Update grades_summary.json with grade components."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="jwts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    crawl_parser = subparsers.add_parser(
        "crawl",
        help="crawl teaching plans and generate course introductions",
        description=(
            "Crawl HIT execution teaching plans and generate or update "
            "course_introductions.json for the courses found in those plans."
        ),
        epilog=("Example:\n  jwts crawl --years 2024 2025 --data-dir ../hoa-major-data"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    crawl_parser.add_argument("--years", nargs="+", required=True, type=_year)
    crawl_parser.add_argument(
        "--no-filename-fallback",
        action="store_true",
        help="fail on duplicate filenames instead of appending major codes",
    )
    crawl_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ".cache" / "jwts",
        help="directory for automatic crawl checkpoints",
    )
    crawl_parser.add_argument(
        "--fresh",
        action="store_true",
        help="discard the unfinished crawl checkpoint and fetch everything again",
    )
    crawl_parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="data directory (defaults to the bundled data directory)",
    )
    crawl_parser.add_argument(
        "--relogin",
        action="store_true",
        help="open Firefox to reauthenticate using saved session state and persist verified cookies",
    )
    crawl_parser.add_argument(
        "--benchmark", action="store_true", help="print total local crawl duration"
    )
    grades_parser = subparsers.add_parser(
        "grades",
        aliases=["crawl-grades"],
        help="crawl the public HIT grade-component summary",
    )
    grades_parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="data directory (defaults to the bundled data directory)",
    )
    grades_parser.add_argument(
        "--relogin",
        action="store_true",
        help="open Firefox to reauthenticate using saved session state and persist verified cookies",
    )
    grades_parser.add_argument(
        "--benchmark", action="store_true", help="print total local crawl duration"
    )
    return parser


def _project_dotenv_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _print_progress(message: str) -> None:
    print(f"progress: {message}", file=sys.stderr, flush=True)


def run_crawl(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    dotenv_path: Path | None = None,
) -> PublicationSummary:
    years = tuple(sorted(set(args.years)))
    settings = Settings.from_sources(environ, dotenv_path or _project_dotenv_path())
    data_dir = Path(args.data_dir)
    checkpoint = CrawlCheckpoint(
        getattr(args, "cache_dir", Path(__file__).resolve().parents[2] / ".cache" / "jwts"),
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
    """Fetch and publish ``grades_summary.json`` from the public grade page."""

    settings = Settings.from_sources(environ, dotenv_path or _project_dotenv_path())
    gateway = TeachingSystemClient(settings, on_progress=_print_progress)
    if getattr(args, "relogin", False):
        gateway.relogin()
    else:
        gateway.refresh_cookie()
    summary = gateway.get_grade_summary()
    return publish_grade_summary(Path(args.data_dir), summary)


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
    started_at = time.perf_counter()
    try:
        if args.command == "crawl":
            summary = run_crawl(args, environ)
        else:
            summary = run_grades(args, environ)
    except HoahrbJwtsError as error:
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
    if args.benchmark:
        print(f"benchmark: crawl_seconds={time.perf_counter() - started_at:.3f}")
    if args.command == "crawl":
        print(f"filename fallbacks: files={len(summary.filename_fallbacks)}")
        for filename in summary.filename_fallbacks:
            print(f"  {filename}")
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
