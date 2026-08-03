"""Authenticated HTTP gateway for the HIT teaching-management system."""

from __future__ import annotations

import time
from collections.abc import Callable
from http.cookies import SimpleCookie
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings
from .errors import AuthenticationError, ParseError, TransportError
from .models import Catalog, Department, Major, PlanPage, SourceCourse
from .parsers import parse_catalog_page, parse_major_list, parse_plan_page


class TeachingSystemClient:
    """Synchronous, authenticated endpoint client used by discovery."""

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "Cookie": settings.cookie,
                "User-Agent": "HOAHRB-maintainer-crawler/1.0",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        retry = Retry(
            total=settings.max_retries,
            backoff_factor=0.5,
            status_forcelist={429, 500, 502, 503, 504},
            respect_retry_after_header=True,
            allowed_methods=frozenset({"GET", "POST"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self._last_request_succeeded = False
        self._on_progress = on_progress
        self._request_count = 0

    def _url(self, path: str) -> str:
        return f"{self.settings.base_url}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        if self._last_request_succeeded and self.settings.delay_seconds:
            time.sleep(self.settings.delay_seconds)
        self._request_count += 1
        request_label = f"request={self._request_count} {method} {path}"
        if self._on_progress is not None:
            self._on_progress(f"{request_label} started")
        started_at = time.perf_counter()
        try:
            response = self.session.request(
                method,
                self._url(path),
                timeout=self.settings.timeout_seconds,
                proxies=self.settings.proxies,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            self._last_request_succeeded = False
            raise TransportError(f"request failed during {path}") from exc

        if response.status_code in {401, 403} or 300 <= response.status_code < 400:
            self._last_request_succeeded = False
            raise AuthenticationError(f"authentication failed during {path}")
        if response.status_code >= 400:
            self._last_request_succeeded = False
            raise TransportError(
                f"teaching system returned HTTP {response.status_code} during {path}"
            )
        self._last_request_succeeded = True
        if self._on_progress is not None:
            self._on_progress(
                f"{request_label} completed seconds={time.perf_counter() - started_at:.3f}"
            )
        return response

    @staticmethod
    def _cookie_pairs(cookie_header: str) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for part in cookie_header.split(";"):
            name, separator, value = part.strip().partition("=")
            if not separator or not name or not value:
                raise AuthenticationError("Cookie is malformed during refresh")
            pairs.append((name, value))
        if not pairs:
            raise AuthenticationError("Cookie is malformed during refresh")
        return pairs

    @staticmethod
    def _set_cookie_headers(response: requests.Response) -> list[str]:
        raw_headers = getattr(response.raw, "headers", None)
        if raw_headers is not None and hasattr(raw_headers, "getlist"):
            return [value for value in raw_headers.getlist("Set-Cookie") if value]
        value = response.headers.get("Set-Cookie")
        return [value] if value else []

    @classmethod
    def _merge_refreshed_cookie(cls, original: str, set_cookie_headers: list[str]) -> str:
        merged = cls._cookie_pairs(original)
        positions = {name: index for index, (name, _) in enumerate(merged)}
        updates: list[tuple[str, str]] = []
        for header in set_cookie_headers:
            parsed = SimpleCookie()
            try:
                parsed.load(header)
            except (TypeError, ValueError):
                continue
            updates.extend((name, morsel.value) for name, morsel in parsed.items() if morsel.value)
        if not updates:
            raise AuthenticationError("Cookie refresh returned no usable Cookie update")
        for name, value in updates:
            if name in positions:
                merged[positions[name]] = (name, value)
            else:
                positions[name] = len(merged)
                merged.append((name, value))
        return "; ".join(f"{name}={value}" for name, value in merged)

    def refresh_cookie(self) -> str:
        """Refresh the existing session Cookie before any business endpoint request."""

        try:
            response = self._request("GET", "/loginCAS")
        except TransportError as exc:
            raise AuthenticationError("Cookie refresh request failed") from exc
        response_text = response.text.lower()
        if "统一身份认证" in response.text or "cas/login" in response_text:
            raise AuthenticationError("Cookie refresh returned a login page")
        refreshed_cookie = self._merge_refreshed_cookie(
            self.session.headers.get("Cookie", ""), self._set_cookie_headers(response)
        )
        self.session.headers["Cookie"] = refreshed_cookie
        return refreshed_cookie

    @staticmethod
    def _json(response: requests.Response, stage: str) -> object:
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise ParseError(f"invalid JSON during {stage}") from exc

    def get_catalog(self) -> Catalog:
        response = self._request("GET", "/zxjh/queryZxkc")
        return parse_catalog_page(response.text)

    def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
        response = self._request(
            "POST",
            "/pub/queryYxzyList_x",
            data={"yxdm": department.code, "nj": year},
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        payload = self._json(response, "major list")
        return parse_major_list(payload, year, department)

    def get_plan(self, major: Major, page_size: int = 500) -> tuple[SourceCourse, ...]:
        if page_size <= 0:
            raise ParseError("page_size must be positive")

        all_courses: list[SourceCourse] = []
        filters = {
            "pageNj": major.year,
            "pageYxdm": major.department_code,
            "pageZydm": major.code,
            "pageKkxn1": "",
            "pageKkxq1": "",
            "pageKkxn": "",
            "pageKkxq": "",
            "pageKcmc": "",
            "pageSize": str(page_size),
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        first_response = self._request("POST", "/zxjh/queryZxkc", data=filters, headers=headers)
        first_page: PlanPage = parse_plan_page(first_response.text)
        if first_page.page_no != 1:
            raise ParseError("execution-plan first page number is not 1")
        all_courses.extend(first_page.courses)
        effective_page_size = first_page.page_size or page_size

        for page_no in range(2, first_page.page_count + 1):
            response = self._request(
                "POST",
                "/zxjh/queryZxkc",
                data={
                    **filters,
                    "pageNo": str(page_no),
                    "pageSize": str(effective_page_size),
                    "pageCount": str(first_page.page_count),
                },
                headers=headers,
            )
            parsed = parse_plan_page(response.text)
            if parsed.page_no != page_no:
                raise ParseError("execution-plan page number does not match the request")
            if parsed.page_count != first_page.page_count:
                raise ParseError("execution-plan page count changed during pagination")
            all_courses.extend(parsed.courses)
        return tuple(all_courses)
