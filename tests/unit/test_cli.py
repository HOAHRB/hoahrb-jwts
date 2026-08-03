from argparse import Namespace
from pathlib import Path

import pytest

from hoa_cli import app
from hoa_cli.config import DEFAULT_DATA_DIR
from hoa_cli.errors import (
    AuthenticationError,
    ConfigError,
    ParseError,
    PublicationError,
    TransportError,
    ValidationError,
)
from hoa_cli.writer import PublicationSummary


def test_cli_requires_years_and_defaults_data_directory_to_original_location() -> None:
    parser = app.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["crawl"])
    with pytest.raises(SystemExit):
        parser.parse_args(["crawl", "--years", "1999", "--data-dir", "out"])
    args = parser.parse_args(["crawl", "--years", "2025"])
    assert args.data_dir == DEFAULT_DATA_DIR


def test_cli_accepts_optional_benchmark_flag() -> None:
    args = app.build_parser().parse_args(
        ["crawl", "--years", "2025", "--data-dir", "out", "--benchmark"]
    )
    assert args.benchmark is True


def test_cli_deduplicates_years_and_calls_boundaries_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeSettings:
        pass

    class FakeGateway:
        pass

    discovered = ["first", "second"]
    normalized = {"first": "n1", "second": "n2"}

    def settings_from_env(environ):
        calls.append("settings")
        return FakeSettings()

    def make_client(settings, on_progress):
        calls.append("client")
        assert on_progress is app._print_progress
        return FakeGateway()

    def discover(gateway, years):
        calls.append(f"discover:{years}")
        return tuple(discovered)

    def normalize(value):
        calls.append(f"normalize:{value}")
        return normalized[value]

    def publish(data_dir, plans, years):
        calls.append(f"publish:{data_dir.name}:{sorted(years)}:{plans}")
        return PublicationSummary(1, 0, 0, 0)

    monkeypatch.setattr(app.Settings, "from_env", settings_from_env)
    monkeypatch.setattr(app, "TeachingSystemClient", make_client)
    monkeypatch.setattr(app, "discover_plans", discover)
    monkeypatch.setattr(app, "normalize_plan", normalize)
    monkeypatch.setattr(app, "publish_plans", publish)
    args = Namespace(years=["2025", "2024", "2025"], data_dir=tmp_path)
    result = app.run_crawl(args, {"HIT_JW_COOKIE": "sanitized"})
    assert result.added == 1
    assert calls == [
        "settings",
        "client",
        "discover:('2024', '2025')",
        "normalize:first",
        "normalize:second",
        f"publish:{tmp_path.name}:['2024', '2025']:{('n1', 'n2')}",
    ]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConfigError("bad config"), 2),
        (AuthenticationError("bad auth"), 2),
        (TransportError("network"), 3),
        (ParseError("shape"), 3),
        (ValidationError("data"), 4),
        (PublicationError("write"), 4),
    ],
)
def test_cli_maps_expected_exit_codes(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: int
) -> None:
    monkeypatch.setattr(app, "run_crawl", lambda args, environ: (_ for _ in ()).throw(error))
    exit_code = app.main(
        ["crawl", "--years", "2025", "--data-dir", "out"], {"HIT_JW_COOKIE": "sanitized"}
    )
    assert exit_code == expected
