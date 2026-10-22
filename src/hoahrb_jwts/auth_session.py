"""基于 requests 的独立会话；受限认证恢复与已验证会话持久化。"""

from http.cookiejar import Cookie
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .auth import MAX_REDIRECTS, checked_target, page_state
from .config import Settings
from .diagnostics import endpoint, logger
from .errors import AuthenticationError, ConfigError, LoginRequired, TransportError
from .session_store import SessionCookieStore


class _NeedsAuthentication(LoginRequired):
    pass


class TeachingSession:
    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        restore_session: bool = True,
    ):
        self.settings = settings
        self._store = None
        self._verified = False
        self._client = session or requests.Session()
        self._client.trust_env = False
        self._client.headers.pop("Cookie", None)
        self._client.cookies.clear()
        self._client.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TodayHIT-JWTS/1.0",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        try:
            if restore_session:
                self._seed_cookie(
                    settings.cookie, urlsplit(settings.base_url).hostname, secure=False
                )
                self._seed_cookie(settings.cas_cookie, "ids.hit.edu.cn", secure=True)
            if settings.cookie_file is not None:
                self._store = SessionCookieStore(settings.cookie_file, settings.base_url)
                if restore_session:
                    self._store.restore(self._client.cookies)
            logger.info(
                "session_loaded teaching_cookies=%d cas_cookies=%d",
                *(
                    sum(
                        not c.is_expired() and c.domain.lstrip(".") == domain
                        for c in self._client.cookies
                    )
                    for domain in (urlsplit(settings.base_url).hostname, "ids.hit.edu.cn")
                ),
            )
        except Exception:
            self._client.close()
            raise

    def _seed_cookie(self, header: str, host: str, *, secure: bool) -> None:
        for part in header.split(";"):
            if not part.strip():
                continue
            name, _, value = part.strip().partition("=")
            self._client.cookies.set_cookie(
                Cookie(
                    version=0,
                    name=name,
                    value=value,
                    port=None,
                    port_specified=False,
                    domain=host,
                    domain_specified=False,
                    domain_initial_dot=False,
                    path="/",
                    path_specified=True,
                    secure=secure,
                    expires=None,
                    discard=True,
                    comment=None,
                    comment_url=None,
                    rest={},
                )
            )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self._client.close()

    def _prepare(self, method: str, url: str, **kwargs):
        request = self._client.prepare_request(
            requests.Request(method, urljoin(self.settings.base_url + "/", url), **kwargs)
        )
        jar = requests.cookies.RequestsCookieJar()
        for cookie in self._client.cookies:
            if cookie.domain.lstrip(".") == urlsplit(request.url).hostname:
                jar.set_cookie(cookie)
        request.headers.pop("Cookie", None)
        header = requests.cookies.get_cookie_header(jar, request)
        if header:
            request.headers["Cookie"] = header
        return request

    def _send(self, method: str, url: str, **kwargs):
        self._verified = False
        try:
            response = self._client.send(
                self._prepare(method, url, **kwargs),
                timeout=self.settings.timeout_seconds,
                proxies=self.settings.proxies,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.warning("request_transport_failed type=%s", type(exc).__name__)
            raise TransportError("teaching system request failed") from None
        logger.info(
            "response method=%s endpoint=%s status=%d bytes=%d set_cookie_present=%s",
            method,
            endpoint(response.url),
            response.status_code,
            len(response.content),
            "Set-Cookie" in response.headers,
        )
        if response.status_code == 401:
            raise _NeedsAuthentication("teaching session requires authentication")
        if response.status_code == 403:
            raise AuthenticationError("teaching system denied access (HTTP 403)")
        if response.status_code >= 400:
            raise TransportError(f"teaching system returned HTTP {response.status_code}")
        return response

    def _request(self, method: str, path: str, **kwargs):
        if not path.startswith("/") or path.startswith("//") or "\\" in path:
            raise ConfigError("请求路径必须是站内绝对路径")
        if set(kwargs) - {"params", "data", "headers"}:
            raise ConfigError("不支持的请求选项")
        response = self._send(method, path, **kwargs)
        visited = set()
        for _ in range(MAX_REDIRECTS + 1):
            interactive, script_target = page_state(response.text)
            target = response.headers.get("Location") if response.is_redirect else script_target
            logger.info("navigation interactive=%s redirect=%s", interactive, bool(target))
            if response.is_redirect and not target:
                raise AuthenticationError("教务跳转缺少目标地址")
            if target:
                target = checked_target(self.settings.base_url, str(response.url), target)
                parsed = urlsplit(target)
                if (
                    method != "GET"
                    or parsed.hostname == "ids.hit.edu.cn"
                    or parsed.path.rstrip("/") in {"/loginCAS", "/login", "/logout"}
                ):
                    raise _NeedsAuthentication("教务系统要求重新认证")
                if target in visited or len(visited) >= MAX_REDIRECTS:
                    raise AuthenticationError("教务跳转循环或超过次数限制")
                visited.add(target)
                response = self._send("GET", target)
                continue
            if interactive:
                raise _NeedsAuthentication("教务系统返回登录页，需要认证")
            return response
        raise AuthenticationError("教务跳转超过次数限制")

    def refresh_cookie(self) -> None:
        """Bounded CAS navigation; never submit a login form or persist unverified cookies."""
        logger.info("cas_recovery_started")
        url = urljoin(self.settings.base_url + "/", "/loginCAS")
        visited = set()
        for _ in range(MAX_REDIRECTS + 1):
            if url in visited:
                raise AuthenticationError("认证跳转循环，未能恢复会话")
            visited.add(url)
            response = self._send("GET", url)
            interactive, script_target = page_state(response.text)
            if interactive:
                logger.warning("cas_interactive_login_required")
                raise LoginRequired("认证需要人工重新登录，请更新教务或统一认证 Cookie")
            target = response.headers.get("Location") if response.is_redirect else script_target
            logger.info("navigation interactive=%s redirect=%s", interactive, bool(target))
            if response.is_redirect and not target:
                raise AuthenticationError("认证跳转缺少目标地址")
            if not target:
                if urlsplit(url).hostname == "ids.hit.edu.cn":
                    raise LoginRequired("统一认证未返回教务系统，需要人工重新登录")
                return
            url = checked_target(self.settings.base_url, str(response.url), target)
            logger.info("cas_redirect endpoint=%s", endpoint(url))
        raise AuthenticationError("认证跳转超过次数限制")

    def _read(self, method: str, path: str, *, refresh: bool, **kwargs):
        # Only public read-only operations opt into this one-time replay.
        try:
            return self._request(method, path, **kwargs)
        except _NeedsAuthentication:
            logger.info("business_auth_required recovery_enabled=%s", refresh)
            if not refresh:
                raise LoginRequired("教务会话需要认证，已禁用自动恢复") from None
        self.refresh_cookie()
        self.check_authentication(refresh=False)
        try:
            return self._request(method, path, **kwargs)
        except _NeedsAuthentication:
            raise LoginRequired("认证恢复后业务请求仍要求登录，请重新登录") from None

    def check_authentication(self, *, refresh: bool = True) -> None:
        """用参考项目已确认的教学计划页验证会话，然后持久化更新。"""
        response = self._read("GET", "/zxjh/queryZxkc", refresh=refresh)
        soup = BeautifulSoup(response.text, "html.parser")
        if (
            "执行教学计划查询" not in soup.get_text()
            or soup.select_one('select[name="pageNj"]') is None
            or soup.select_one('select[name="pageYxdm"]') is None
        ):
            raise AuthenticationError("未识别到已登录页面，Cookie 有效性尚未确认")
        self._verified = True
        logger.info("authentication_verified")
        self.persist_verified_cookie()

    def persist_verified_cookie(self) -> None:
        """仅允许已验证会话写入独立 JSON。"""
        if not self._verified:
            raise AuthenticationError("尚未成功验证会话，不能持久化 Cookie")
        if self._store is not None:
            self._store.save(self._client.cookies)
            logger.info("verified_session_saved")
