"""Bounded CAS navigation policy shared by the HTTP client and its tests."""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .errors import AuthenticationError

CAS_ORIGIN = "https://ids.hit.edu.cn"
MAX_REDIRECTS = 8
_SCRIPT_TARGET = re.compile(
    r"(?:\b(?:window\.|top\.|self\.|parent\.)?location(?:\.href)?\s*=\s*"
    r"|\b(?:window\.|top\.|self\.|parent\.)?location\.(?:replace|assign)\s*\(\s*)"
    r"(['\"])([^'\"\r\n]+)\1",
    re.IGNORECASE,
)


def checked_target(base_url: str, current_url: str, target: str) -> str:
    """Allow the configured teaching origin and HTTPS CAS only; never log tickets."""
    try:
        if not target.strip() or "\\" in target or any(ord(c) < 32 for c in target):
            raise ValueError
        resolved = urljoin(current_url, target)
        url = urlsplit(resolved)
        base = urlsplit(base_url)
        origin = (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80))
        teaching = (
            base.scheme,
            base.hostname,
            base.port or (443 if base.scheme == "https" else 80),
        )
        upgraded = (
            ("https", base.hostname, 443)
            if teaching[0] == "http" and teaching[2] == 80
            else teaching
        )
        if (
            url.username
            or url.password
            or url.fragment
            or origin not in {teaching, upgraded, ("https", "ids.hit.edu.cn", 443)}
        ):
            raise ValueError
        if (
            urlsplit(current_url).scheme == "https"
            and url.scheme != "https"
            and not (urlsplit(current_url).hostname == "ids.hit.edu.cn" and origin == teaching)
        ):
            raise ValueError
        return resolved
    except ValueError:
        raise AuthenticationError("认证跳转目标不受信任或存在不安全降级，已停止") from None


def page_state(text: str) -> tuple[bool, str | None]:
    """Recognize interactive login and literal JS/meta navigation, without executing JS."""
    if not text.lstrip().startswith("<"):
        return False, None
    soup = BeautifulSoup(text, "html.parser")
    interactive = bool(
        soup.select_one(
            'input[type="password"], input[name="captcha"], input[name="captchaResponse"]'
        )
    )
    interactive = interactive or any(
        marker in text for marker in ("统一身份认证", "请先登录", "登录已超时", "会话已过期")
    )
    # Do not interpret navigation helpers embedded in a full business page.
    if len(text) <= 8192:
        for script in soup.find_all("script"):
            match = _SCRIPT_TARGET.search(script.get_text())
            if match:
                return interactive, match[2]
            if re.search(r"\blocation(?:\.href)?\s*=", script.get_text(), re.I):
                # Legacy session-expiry stubs concatenate a context path. Treat
                # these as auth requirements rather than executing arbitrary JS.
                interactive = True
        for meta in soup.find_all("meta"):
            if str(meta.get("http-equiv", "")).lower() == "refresh":
                match = re.search(r"(?:^|;)\s*url\s*=\s*(.+)$", str(meta.get("content", "")), re.I)
                if match:
                    return interactive, match[1].strip(" '\"")
    return interactive, None
