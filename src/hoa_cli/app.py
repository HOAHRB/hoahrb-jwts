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
from .config import Settings
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
        default=Path("."),
        help="data directory (defaults to the current working directory)",
    )
    crawl_parser.add_argument(
        "--benchmark", action="store_true", help="print total local crawl duration"
    )
    return parser


def _environment_with_dotenv(environ: Mapping[str, str]) -> dict[str, str]:
    values = dict(environ)
    try:
        from dotenv import dotenv_values

        env_path = Path(__file__).resolve().parents[2] / ".env"
        for key, value in dotenv_values(env_path).items():
            if value is not None and key not in values:
                values[key] = value
    except (ImportError, OSError):
        pass
    return values


def _print_progress(message: str) -> None:
    print(f"progress: {message}", file=sys.stderr, flush=True)


def run_crawl(args: argparse.Namespace, environ: Mapping[str, str]) -> PublicationSummary:
    years = tuple(sorted(set(args.years)))
    settings = Settings.from_env(_environment_with_dotenv(environ))
    gateway = TeachingSystemClient(settings, on_progress=_print_progress)
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
