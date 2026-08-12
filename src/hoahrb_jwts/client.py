"""Authenticated HTTP gateway for the HIT teaching-management system."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import replace
from http.cookies import SimpleCookie
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings
from .course_introduction import CourseIntroductions, parse_course_introduction
from .errors import AuthenticationError, ParseError, TransportError
from .grade_summary import (
    GradeDetailReference,
    GradeSummary,
    merge_grade_summaries,
    parse_grade_summary_detail,
    parse_grade_summary_page,
)
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
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
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

        if response.status_code in {401, 403}:
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
            if not part.strip():
                continue
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
            values = [value for value in raw_headers.getlist("Set-Cookie") if value]
            if values:
                return values
        value = response.headers.get("Set-Cookie")
        if value:
            return [value]
        return [f"{name}={cookie.value}" for name, cookie in response.cookies.items()]

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
            return original
        for name, value in updates:
            if name in positions:
                merged[positions[name]] = (name, value)
            else:
                positions[name] = len(merged)
                merged.append((name, value))
        return "; ".join(f"{name}={value}" for name, value in merged)

    def _activate_cookie(self, cookie: str) -> None:
        if not cookie.strip():
            raise AuthenticationError("Cookie refresh left no usable Cookie")
        self.session.headers["Cookie"] = cookie
        self.settings = replace(self.settings, cookie=cookie)

    def refresh_cookie(self) -> str:
        """Synchronize an optional CAS Cookie update before business requests."""

        try:
            response = self._request("GET", "/loginCAS")
        except TransportError as exc:
            raise AuthenticationError("Cookie refresh request failed") from exc
        refreshed_cookie = self._merge_refreshed_cookie(
            self.session.headers.get("Cookie", ""), self._set_cookie_headers(response)
        )
        self._activate_cookie(refreshed_cookie)
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

    def get_course_introductions(self, course_codes: Collection[str]) -> CourseIntroductions:
        """Fetch bilingual introductions for sorted unique course codes."""

        introductions: CourseIntroductions = {}
        for course_code in sorted(set(course_codes)):
            response = self._request("GET", "/pub/queryKcxxView", params={"kcdm": course_code})
            introductions[course_code] = {
                "default": parse_course_introduction(response.text, course_code)
            }
        return introductions

    def get_grade_summary(self, page_size: int = 500) -> GradeSummary:
        """Fetch grade-component weights from ``/cjcx/queryQmcj``.

        The list page provides only final scores and ``queryCjView`` arguments.
        Each magnifying-glass link is subsequently fetched from
        ``/cjcx/queryCjxxView`` to obtain actual component weights.
        """

        if page_size <= 0:
            raise ParseError("page_size must be positive")

        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

        def request_page(
            method: str,
            page_no: int,
            page_count: int | None = None,
            requested_page_size: int = page_size,
        ) -> object:
            if method == "GET":
                params: dict[str, str] = {}
                if page_no > 1:
                    params = {
                        "pageNo": str(page_no),
                        "pageSize": str(requested_page_size),
                    }
                    if page_count is not None:
                        params["pageCount"] = str(page_count)
                response = self._request("GET", "/cjcx/queryQmcj", params=params)
            else:
                data = {"pageSize": str(requested_page_size)}
                if page_no > 1:
                    data.update({"pageNo": str(page_no)})
                    if page_count is not None:
                        data["pageCount"] = str(page_count)
                response = self._request("POST", "/cjcx/queryQmcj", data=data, headers=headers)
            return response

        method = "GET"
        try:
            response = request_page(method, 1)
        except TransportError:
            method = "POST"
            response = request_page(method, 1)
            first = parse_grade_summary_page(response.text)
        else:
            try:
                first = parse_grade_summary_page(response.text)
            except ParseError:
                content_type = response.headers.get("Content-Type", "").lower()
                if "html" not in content_type and not response.text.lstrip().startswith("<"):
                    raise
                method = "POST"
                response = request_page(method, 1)
                first = parse_grade_summary_page(response.text)

        if first.page_no != 1:
            raise ParseError("grade-summary first page number is not 1")
        all_grades: GradeSummary = {}
        merge_grade_summaries(all_grades, first.summary)
        detail_references: list[GradeDetailReference] = list(first.detail_references)
        seen_detail_references = {
            (reference.id, reference.rwh, reference.jhh) for reference in detail_references
        }
        effective_page_size = first.page_size or page_size
        pagination_method = method
        is_html_page = "html" in response.headers.get(
            "Content-Type", ""
        ).lower() or response.text.lstrip().startswith("<")
        if first.page_count > 1 and method == "GET" and is_html_page:
            pagination_method = "POST"

        for page_no in range(2, first.page_count + 1):
            response = request_page(
                pagination_method, page_no, first.page_count, effective_page_size
            )
            parsed = parse_grade_summary_page(response.text)
            if parsed.page_no != page_no:
                # queryQmcj renders the hidden pageNo input without a value on
                # later pages, despite returning the requested page content.
                # The POST request itself remains the authoritative page index.
                if pagination_method == "POST" and parsed.page_no == 1:
                    parsed = replace(parsed, page_no=page_no)
                else:
                    raise ParseError("grade-summary page number does not match the request")
            if parsed.page_count != first.page_count:
                raise ParseError("grade-summary page count changed during pagination")
            merge_grade_summaries(all_grades, parsed.summary)
            for reference in parsed.detail_references:
                identity = (reference.id, reference.rwh, reference.jhh)
                if identity not in seen_detail_references:
                    seen_detail_references.add(identity)
                    detail_references.append(reference)

        for reference in detail_references:
            response = self._request(
                "GET",
                "/cjcx/queryCjxxView",
                params={"id": reference.id, "rwh": reference.rwh, "jhh": reference.jhh},
            )
            merge_grade_summaries(all_grades, parse_grade_summary_detail(response.text))

        if not all_grades:
            raise ParseError("grade-summary response has no recognizable course records")
        return all_grades
