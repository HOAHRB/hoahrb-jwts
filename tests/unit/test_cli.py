from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hoahrb_jwts import app
from hoahrb_jwts.config import DEFAULT_DATA_DIR, CookieSource
from hoahrb_jwts.errors import (
    AuthenticationError,
    ConfigError,
    ParseError,
    PublicationError,
    TransportError,
    ValidationError,
)
from hoahrb_jwts.writer import GradePublicationSummary, PublicationSummary


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


def test_cli_accepts_relogin_flag() -> None:
    args = app.build_parser().parse_args(["crawl", "--years", "2025", "--relogin"])

    assert args.relogin is True


def test_cli_accepts_grade_crawl_aliases() -> None:
    assert app.build_parser().parse_args(["grades"]).command == "grades"
    assert app.build_parser().parse_args(["crawl-grades"]).command == "crawl-grades"


def test_grade_crawl_publishes_through_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeSettings:
        cookie_source = CookieSource.PROCESS_ENV
        cookie_file = None
        cookie = "JSESSIONID=old"

    class FakeGateway:
        def refresh_cookie(self):
            calls.append("refresh")
            return "JSESSIONID=refreshed"

        def get_grade_summary(self):
            calls.append("grades")
            return {"C001": {"default": [{"name": "考试", "percent": "100%"}]}}

    monkeypatch.setattr(app.Settings, "from_sources", lambda *args: FakeSettings())
    monkeypatch.setattr(app, "TeachingSystemClient", lambda *args, **kwargs: FakeGateway())
    monkeypatch.setattr(
        app,
        "publish_grade_summary",
        lambda data_dir, summary: (
            calls.append(f"publish:{data_dir.name}:{sorted(summary)}")
            or GradePublicationSummary(1, 1, 0, 0)
        ),
    )

    result = app.run_grades(
        Namespace(data_dir=tmp_path, relogin=False),
        {"HIT_JW_COOKIE": "sanitized"},
    )

    assert result.courses == 1
    assert calls == ["refresh", "grades", f"publish:{tmp_path.name}:['C001']"]


def test_grade_crawl_leaves_verified_persistence_to_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    dotenv_path = tmp_path / ".env"

    class FakeSettings:
        cookie_source = CookieSource.DOTENV
        cookie_file = dotenv_path
        cookie = "JSESSIONID=old"

    class FakeGateway:
        def refresh_cookie(self):
            calls.append("refresh")
            return "JSESSIONID=refreshed"

        def get_course_introductions(self, course_codes):
            calls.append(f"introductions:{sorted(course_codes)}")
            return {"C001": {"default": {"zh": "简介", "en": "intro"}}}

        def get_grade_summary(self):
            calls.append("grades")
            raise AuthenticationError("expired")

    monkeypatch.setattr(app.Settings, "from_sources", lambda *args: FakeSettings())
    monkeypatch.setattr(app, "TeachingSystemClient", lambda *args, **kwargs: FakeGateway())

    with pytest.raises(AuthenticationError, match="expired"):
        app.run_grades(
            Namespace(data_dir=tmp_path, relogin=False),
            {"HIT_JW_COOKIE": "sanitized"},
        )

    assert calls == ["refresh", "grades"]


def test_cli_deduplicates_years_and_calls_boundaries_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeSettings:
        cookie_source = CookieSource.PROCESS_ENV
        cookie_file = None

    class FakeGateway:
        def refresh_cookie(self):
            calls.append("refresh")
            return "JSESSIONID=refreshed"

        def get_course_introductions(self, course_codes):
            calls.append(f"introductions:{sorted(course_codes)}")
            return {"C001": {"default": {"zh": "简介", "en": "intro"}}}

    discovered = ["first", "second"]
    normalized = {
        "first": SimpleNamespace(courses=(SimpleNamespace(course_code="C001"),)),
        "second": SimpleNamespace(courses=(SimpleNamespace(course_code="C001"),)),
    }

    def settings_from_sources(environ, dotenv_path):
        calls.append("settings")
        return FakeSettings()

    def make_client(settings, on_progress, checkpoint):
        calls.append("client")
        assert on_progress is app._print_progress
        return FakeGateway()

    def discover(gateway, years):
        calls.append(f"discover:{years}")
        return tuple(discovered)

    def normalize(value):
        calls.append(f"normalize:{value}")
        return normalized[value]

    def publish(data_dir, plans, years, introductions, allow_filename_fallback=True):
        assert allow_filename_fallback is True
        calls.append(
            f"publish:{data_dir.name}:{sorted(years)}:{len(plans)}:{sorted(introductions)}"
        )
        return PublicationSummary(1, 0, 0, 0)

    monkeypatch.setattr(app.Settings, "from_sources", settings_from_sources)
    monkeypatch.setattr(app, "TeachingSystemClient", make_client)
    monkeypatch.setattr(app, "discover_plans", discover)
    monkeypatch.setattr(app, "normalize_plan", normalize)
    monkeypatch.setattr(app, "publish_plans", publish)
    args = Namespace(years=["2025", "2024", "2025"], data_dir=tmp_path, relogin=False)
    result = app.run_crawl(args, {"HIT_JW_COOKIE": "sanitized"})
    assert result.added == 1
    assert calls == [
        "settings",
        "client",
        "refresh",
        "discover:('2024', '2025')",
        "normalize:first",
        "normalize:second",
        "introductions:['C001']",
        f"publish:{tmp_path.name}:['2024', '2025']:2:['C001']",
    ]


def test_crawl_does_not_overwrite_gateway_credentials_after_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("HIT_JW_COOKIE=JSESSIONID=old\n", encoding="utf-8")
    calls: list[str] = []

    class FakeGateway:
        def refresh_cookie(self):
            calls.append("refresh")
            return "JSESSIONID=refreshed"

    def make_client(settings, on_progress, checkpoint):
        assert settings.cookie_source is CookieSource.DOTENV
        return FakeGateway()

    def persist(path, cookie):
        calls.append(f"persist:{path.name}:{cookie}")

    def discover(gateway, years):
        calls.append("discover")
        return ()

    monkeypatch.setattr(app, "TeachingSystemClient", make_client)
    monkeypatch.setattr(app, "discover_plans", discover)
    monkeypatch.setattr(
        app, "publish_plans", lambda *args, **kwargs: PublicationSummary(0, 0, 0, 0)
    )
    args = Namespace(years=["2025"], data_dir=tmp_path, relogin=False)

    app.run_crawl(args, {}, dotenv_path=dotenv_path)

    assert calls == ["refresh", "discover"]


def test_crawl_does_not_persist_process_environment_cookie(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeSettings:
        cookie_source = CookieSource.PROCESS_ENV
        cookie_file = None

    class FakeGateway:
        def refresh_cookie(self):
            calls.append("refresh")
            return "JSESSIONID=refreshed"

    monkeypatch.setattr(app.Settings, "from_sources", lambda *args: FakeSettings())
    monkeypatch.setattr(app, "TeachingSystemClient", lambda *args, **kwargs: FakeGateway())
    monkeypatch.setattr(app, "discover_plans", lambda *args: ())
    monkeypatch.setattr(
        app, "publish_plans", lambda *args, **kwargs: PublicationSummary(0, 0, 0, 0)
    )

    app.run_crawl(
        Namespace(years=["2025"], data_dir=tmp_path, relogin=False),
        {"HIT_JW_COOKIE": "JSESSIONID=process"},
    )

    assert calls == ["refresh"]


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


@pytest.mark.parametrize("command", ["crawl", "grades"])
def test_relogin_opens_browser_instead_of_normal_startup_check(monkeypatch, tmp_path, command):
    calls = []
    gateway = SimpleNamespace(
        refresh_cookie=lambda: pytest.fail("normal check must be skipped"),
        relogin=lambda: calls.append("relogin"),
        get_grade_summary=lambda: {},
    )
    monkeypatch.setattr(app.Settings, "from_sources", lambda *args: SimpleNamespace())
    monkeypatch.setattr(app, "TeachingSystemClient", lambda *args, **kwargs: gateway)
    monkeypatch.setattr(app, "discover_plans", lambda *args: ())
    monkeypatch.setattr(
        app, "publish_plans", lambda *args, **kwargs: PublicationSummary(0, 0, 0, 0)
    )
    monkeypatch.setattr(
        app, "publish_grade_summary", lambda *args: GradePublicationSummary(0, 0, 0, 0)
    )
    args = Namespace(years=["2025"], data_dir=tmp_path, cache_dir=tmp_path / "cache", relogin=True)
    if command == "crawl":
        app.run_crawl(args, {})
    else:
        app.run_grades(args, {})
    assert calls == ["relogin"]
