"""Interactive sign-in at startup only; runtime queries never open browsers."""

import threading
import time
from http.cookiejar import Cookie
from urllib.parse import unquote, urlsplit

from .auth_session import TeachingSession
from .config import Settings
from .errors import AuthenticationError, ConfigError, LoginRequired

AUTHENTICATION_LOCK = threading.RLock()


def launch_login_browser(runtime, launch_options: dict):
    """Prefer installed system browsers; never download a browser automatically."""
    from playwright.sync_api import Error as BrowserError

    candidates = (
        ("chromium", "msedge"),
        ("chromium", "chrome"),
        ("chromium", None),
        ("webkit", None),
    )
    for name, channel in candidates:
        try:
            options = dict(launch_options)
            if channel:
                options["channel"] = channel
            return getattr(runtime, name).launch(**options)
        except BrowserError:
            continue
    raise ConfigError(
        "没有可启动的浏览器；可使用已安装的 Edge/Chrome，或运行 uv run playwright install chromium，并确认存在桌面会话"
    )


def import_browser_cookies(session: TeachingSession, cookies: list[dict]) -> None:
    allowed = {urlsplit(session.settings.base_url).hostname, "ids.hit.edu.cn"}
    session._client.cookies.clear()
    for item in cookies:
        domain = item["domain"]
        if domain.lstrip(".") not in allowed:
            continue
        if domain.lstrip(".") == "ids.hit.edu.cn" and not item["secure"]:
            continue
        expiry = item.get("expires", -1)
        session._client.cookies.set_cookie(
            Cookie(
                version=0,
                name=item["name"],
                value=item["value"],
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=domain.startswith("."),
                domain_initial_dot=domain.startswith("."),
                path=item["path"],
                path_specified=True,
                secure=item["secure"],
                expires=int(expiry) if expiry > 0 else None,
                discard=expiry <= 0,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": item.get("httpOnly", False)},
            )
        )


def export_browser_cookies(session: TeachingSession) -> list[dict]:
    """Restore the verified cookie state into a fresh isolated browser context."""
    result = []
    for cookie in session._client.cookies:
        if cookie.is_expired():
            continue
        item = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
            "httpOnly": cookie.has_nonstandard_attr("HttpOnly"),
            "sameSite": "Lax",
        }
        if cookie.expires is not None:
            item["expires"] = cookie.expires
        result.append(item)
    return result


def browser_login(settings: Settings, *, timeout_seconds: float = 300) -> None:
    """Use an available isolated browser; verify cookies before writing any file."""
    if settings.cookie_file is None:
        raise ConfigError("浏览器登录需要已配置的会话文件路径")
    try:
        from playwright.sync_api import Error as BrowserError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ConfigError("浏览器登录需要安装 playwright：uv sync") from None
    try:
        with sync_playwright() as runtime:
            launch_options = {"headless": False}
            proxy = settings.proxies.get("https") or settings.proxies.get("http")
            if proxy:
                parsed = urlsplit(proxy)
                if not parsed.hostname or parsed.scheme not in {"http", "https", "socks5"}:
                    raise ConfigError("浏览器代理配置无效")
                server = f"{parsed.scheme}://{parsed.hostname}"
                if parsed.port:
                    server += f":{parsed.port}"
                launch_options["proxy"] = {"server": server}
                if parsed.username:
                    launch_options["proxy"].update(
                        username=unquote(parsed.username), password=unquote(parsed.password or "")
                    )
            browser = launch_login_browser(runtime, launch_options)
            try:
                # Keep the engine's real UA (including its installed version and
                # platform) rather than spoofing a stale, inconsistent UA string.
                context = browser.new_context(
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1366, "height": 768},
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
                )
                with TeachingSession(settings) as saved:
                    context.add_cookies(export_browser_cookies(saved))
                page = context.new_page()
                page.goto(
                    settings.base_url + "/loginCAS", wait_until="domcontentloaded", timeout=30000
                )
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    if page.is_closed():
                        raise LoginRequired("登录窗口已关闭，未更新凭据")
                    if urlsplit(page.url).hostname == urlsplit(settings.base_url).hostname:
                        with TeachingSession(settings) as session:
                            import_browser_cookies(session, context.cookies())
                            try:
                                session.check_authentication(refresh=False)
                            except AuthenticationError:
                                pass
                            else:
                                return
                    page.wait_for_timeout(1000)
                raise LoginRequired("浏览器登录等待超时，未更新凭据")
            finally:
                browser.close()
    except BrowserError:
        raise ConfigError("浏览器登录未完成，请检查窗口和站点连接；也可手动更新 Cookie") from None


def ensure_startup_authentication(settings: Settings) -> None:
    """Check once at startup, open one login window only for expired credentials."""
    with AUTHENTICATION_LOCK:
        try:
            with TeachingSession(settings) as session:
                session.check_authentication()
        except LoginRequired:
            browser_login(settings)
