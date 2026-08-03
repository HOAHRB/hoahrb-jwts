"""The single supported maintainer-run ``hoa crawl`` command."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .client import TeachingSystemClient
from .config import DEFAULT_DATA_DIR, CookieSource, Settings
from .cookie_store import persist_dotenv_cookie
from .discovery import discover_plans
from .errors import (
    AuthenticationError,
    ConfigError,
    HoaCliError,
    ParseError,
    PublicationError,
    TransportError,
    ValidationError,
)
from .normalize import normalize_plan
from .writer import PublicationSummary, publish_plans


def _year(value: str) -> str:
    if not re.fullmatch(r"20\d{2}", value):
        raise argparse.ArgumentTypeError("year must match 20XX")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HOA HIT academic-plan crawler")
    parser.add_argument("--version", action="version", version="hoa-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    crawl_parser = subparsers.add_parser("crawl", help="crawl HIT execution teaching plans")
    crawl_parser.add_argument("--years", nargs="+", required=True, type=_year)
    crawl_parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="data directory (defaults to the bundled data directory)",
    )
    crawl_parser.add_argument(
        "--no-refresh-cookie",
        action="store_true",
        help="use HIT_JW_COOKIE unchanged instead of refreshing it through CAS",
    )
    crawl_parser.add_argument(
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
    gateway = TeachingSystemClient(settings, on_progress=_print_progress)
    if not args.no_refresh_cookie:
        refreshed_cookie = gateway.refresh_cookie()
        if settings.cookie_source is CookieSource.DOTENV:
            if settings.cookie_file is None:
                raise ConfigError(".env Cookie source has no persistence path")
            persist_dotenv_cookie(settings.cookie_file, refreshed_cookie)
    discovered = discover_plans(gateway, years)
    normalized = tuple(normalize_plan(plan) for plan in discovered)
    return publish_plans(Path(args.data_dir), normalized, set(years))


def _exit_code(error: HoaCliError) -> int:
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
        summary = run_crawl(args, environ)
    except HoaCliError as error:
        print(f"error: {error}", file=sys.stderr)
        return _exit_code(error)
    print(
        "published plans: "
        f"added={summary.added} updated={summary.updated} "
        f"removed={summary.removed} unchanged={summary.unchanged}"
    )
    if args.benchmark:
        print(f"benchmark: crawl_seconds={time.perf_counter() - started_at:.3f}")
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
