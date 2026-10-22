from unittest.mock import MagicMock, call

import pytest
import responses
from playwright.sync_api import Error

from hoahrb_jwts.auth import page_state
from hoahrb_jwts.auth_session import TeachingSession
from hoahrb_jwts.browser_login import launch_login_browser
from hoahrb_jwts.diagnostics import configure_auth_logging, endpoint, logger
from hoahrb_jwts.errors import LoginRequired


def test_repeated_bom_login_page():
    assert page_state("\ufeff\ufeff<!DOCTYPE html><input type=password>") == (True, None)


def test_existing_system_browser_selection():
    runtime = MagicMock()
    runtime.chromium.launch.side_effect = [Error("Edge missing"), runtime]
    assert launch_login_browser(runtime, {"headless": False}) is runtime
    assert runtime.chromium.launch.call_args_list == [
        call(headless=False, channel="msedge"),
        call(headless=False, channel="chrome"),
    ]
    runtime.firefox.launch.assert_not_called()
    runtime.webkit.launch.assert_not_called()


@responses.activate
def test_authentication_log_is_redacted(settings, tmp_path):
    old_handlers, old_level = logger.handlers[:], logger.level
    logger.handlers = []
    path = tmp_path / "auth.log"
    try:
        configure_auth_logging(path)
        configure_auth_logging(path)
        assert len(logger.handlers) == 1
        responses.get("http://jwts.hit.edu.cn/zxjh/queryZxkc", status=401)
        responses.get(
            "http://jwts.hit.edu.cn/loginCAS",
            status=302,
            headers={"Location": "https://ids.hit.edu.cn/authserver/login?ticket=secret-ticket"},
        )
        responses.get(
            "https://ids.hit.edu.cn/authserver/login?ticket=secret-ticket",
            body='\ufeff\ufeff<input type=password value="secret-password">',
        )
        with TeachingSession(settings) as session:
            with pytest.raises(LoginRequired):
                session.check_authentication()
        contents = path.read_text(encoding="utf-8")
        assert "cas_interactive_login_required" in contents
        assert "cas_recovery_started" in contents
        for secret in ("secret-ticket", "secret-password", "sanitized"):
            assert secret not in contents
        assert (
            endpoint("https://ids.hit.edu.cn/private-secret?ticket=secret")
            == "ids.hit.edu.cn/<other>"
        )
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = old_handlers
        logger.setLevel(old_level)
