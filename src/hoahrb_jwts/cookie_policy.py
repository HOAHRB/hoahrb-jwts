"""Cookie scope shared by browser import, storage, and HTTP requests."""

from urllib.parse import urlsplit


def accepted(domain: str, secure: bool, domain_specified: bool, base_url: str) -> bool:
    host = domain.lstrip(".").lower()
    teaching = urlsplit(base_url).hostname
    if host == "hit.edu.cn":
        return bool(domain_specified and teaching and teaching.endswith(".hit.edu.cn"))
    return host in {teaching, "ids.hit.edu.cn"} and (host != "ids.hit.edu.cn" or secure)


def matches(cookie, host: str, base_url: str) -> bool:
    if not accepted(cookie.domain, cookie.secure, cookie.domain_specified, base_url):
        return False
    domain = cookie.domain.lstrip(".").lower()
    if host not in {urlsplit(base_url).hostname, "ids.hit.edu.cn"}:
        return False
    return domain == host or (domain == "hit.edu.cn" and cookie.domain_specified)
