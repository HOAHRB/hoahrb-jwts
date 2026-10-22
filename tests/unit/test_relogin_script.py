import importlib.util
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import responses

from hoahrb_jwts.auth_session import TeachingSession
from hoahrb_jwts.browser_login import (
    _NavigationLoop,
    browser_login,
    export_browser_cookies,
    import_browser_cookies,
)
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import LoginRequired

_spec = importlib.util.spec_from_file_location(
    "jwts_relogin", Path(__file__).resolve().parents[2] / "relogin.py"
)
relogin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relogin)


def test_help_has_no_login_side_effects(capsys):
    with (
        patch.object(relogin, "browser_login") as login,
        patch.object(relogin, "configure_auth_logging") as logs,
    ):
        with pytest.raises(SystemExit) as result:
            relogin.main(["--help"])
        assert result.value.code == 0
        login.assert_not_called()
        logs.assert_not_called()
    assert "--fresh" in capsys.readouterr().out


@pytest.mark.parametrize("args,fresh", [([], False), (["--fresh"], True)])
def test_relogin_script_options(args, fresh):
    with (
        patch.object(relogin.Settings, "from_sources") as settings,
        patch.object(relogin, "browser_login") as login,
        patch.object(relogin, "configure_auth_logging"),
    ):
        assert relogin.main(args) == 0
        login.assert_called_once_with(settings.return_value, fresh=fresh)


def test_login_failure_returns_nonzero():
    with (
        patch.object(relogin.Settings, "from_sources"),
        patch.object(relogin, "configure_auth_logging"),
        patch.object(relogin, "browser_login", side_effect=LoginRequired("closed")),
    ):
        assert relogin.main([]) == 1


@responses.activate
def test_fresh_session_keeps_original_until_verified(tmp_path):
    path = tmp_path / "session.json"
    original = b"old or corrupt credentials"
    path.write_bytes(original)
    settings = make_settings(cookie="SID=old", cas_cookie="TGC=old", cookie_file=path)
    responses.get("http://jwts.hit.edu.cn/zxjh/queryZxkc", status=401)
    with TeachingSession(settings, restore_session=False) as session:
        with pytest.raises(LoginRequired):
            session.check_authentication(refresh=False)
    assert "Cookie" not in responses.calls[0].request.headers
    assert path.read_bytes() == original


def test_fresh_browser_never_imports_saved_cookies(tmp_path):
    runtime = MagicMock()
    page = runtime.chromium.launch.return_value.new_context.return_value.new_page.return_value
    page.url = "http://jwts.hit.edu.cn/main"
    page.is_closed.return_value = False
    with (
        patch("playwright.sync_api.sync_playwright") as playwright,
        patch("hoahrb_jwts.browser_login.TeachingSession") as session,
    ):
        playwright.return_value.__enter__.return_value = runtime
        session.return_value.__enter__.return_value.settings = make_settings()
        browser_login(make_settings(cookie_file=tmp_path / "session.json"), fresh=True)
        runtime.chromium.launch.return_value.new_context.return_value.add_cookies.assert_not_called()
        assert all(call.kwargs == {"restore_session": False} for call in session.call_args_list)
        session.return_value.__enter__.return_value.check_authentication.assert_called_once_with(
            refresh=False
        )


def test_fast_navigation_loop_only():
    loop = _NavigationLoop()
    with patch("hoahrb_jwts.browser_login.time.monotonic", return_value=10):
        for _ in range(7):
            loop.record()
        assert not loop.detected()
        loop.record()
        assert loop.detected()
    with patch("hoahrb_jwts.browser_login.time.monotonic", return_value=16):
        assert not loop.detected()


def test_login_loop_clears_browser_cookies_without_reopening(tmp_path):
    runtime = MagicMock()
    browser = runtime.chromium.launch.return_value
    context = browser.new_context.return_value
    page = context.new_page.return_value
    page.url = "http://jwts.hit.edu.cn/main"
    page.is_closed.return_value = False

    def navigate(*args, **kwargs):
        if page.goto.call_count == 1:
            callback = page.on.call_args.args[1]
            for _ in range(8):
                callback(page.main_frame)

    page.goto.side_effect = navigate
    with (
        patch("playwright.sync_api.sync_playwright") as playwright,
        patch("hoahrb_jwts.browser_login.TeachingSession") as session,
    ):
        playwright.return_value.__enter__.return_value = runtime
        session.return_value.__enter__.return_value.settings = make_settings()
        browser_login(make_settings(cookie_file=tmp_path / "session.json"), fresh=True)
        context.clear_cookies.assert_called_once()
        assert page.goto.call_count == 2
        runtime.chromium.launch.assert_called_once()


def test_http_only_round_trip_and_legacy_false():
    with TeachingSession(make_settings()) as session:
        import_browser_cookies(
            session,
            [
                {
                    "name": name,
                    "value": "x",
                    "domain": "ids.hit.edu.cn",
                    "path": "/",
                    "secure": True,
                    "httpOnly": flag,
                }
                for name, flag in [("visible", False), ("hidden", True)]
            ],
        )
        session._seed_cookie("legacy=x", "ids.hit.edu.cn", secure=True)
        legacy = next(c for c in session._client.cookies if c.name == "legacy")
        legacy.set_nonstandard_attr("HttpOnly", False)
        exported = {c["name"]: c for c in export_browser_cookies(session)}
        assert exported["visible"]["httpOnly"] is False
        assert exported["hidden"]["httpOnly"] is True
        assert exported["legacy"]["httpOnly"] is False


def test_cas_locale_is_regenerated_without_discarding_authentication():
    with TeachingSession(make_settings()) as session:
        session._seed_cookie(
            "org.springframework.web.servlet.i18n.CookieLocaleResolver.LOCALE=zh_CN; TGC=auth",
            "ids.hit.edu.cn",
            secure=True,
        )
        assert [c["name"] for c in export_browser_cookies(session)] == ["TGC"]


def make_settings(**kwargs):
    options = {"cookie_file": None, **kwargs}
    return replace(Settings.from_env({}), **options)
