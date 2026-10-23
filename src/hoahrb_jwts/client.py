"""Authenticated HTTP gateway for the HIT teaching-management system."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Collection
from dataclasses import replace
from typing import Any, TypeVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .auth_session import TeachingSession
from .browser_login import browser_login
from .checkpoint import CrawlCheckpoint
from .config import Settings
from .course_introduction import CourseIntroductions, parse_course_introduction
from .data_merge import parse_default_plan_identity, parse_student_profile
from .errors import AuthenticationError, LoginRequired, ParseError, TransportError
from .grade_summary import (
    GradeDetailReference,
    GradeSummary,
    merge_grade_summaries,
    parse_grade_summary_detail,
    parse_grade_summary_page,
)
from .models import Catalog, Department, Major, PlanPage, SourceCourse
from .parsers import parse_catalog_page, parse_major_list, parse_plan_page

T = TypeVar("T")


class TeachingSystemClient:
    """Synchronous, authenticated endpoint client used by discovery."""

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        on_progress: Callable[[str], None] | None = None,
        checkpoint: CrawlCheckpoint | None = None,
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
        self._checkpoint = checkpoint
        self._auth = TeachingSession(settings, session=self.session)
        self.recover_authentication = True

    def _parsed_request(
        self, method: str, path: str, parse: Callable[[str], T], **kwargs: Any
    ) -> T:
        def fetch() -> str:
            return self._request(method, path, **kwargs).text

        if self._checkpoint is None:
            parsed = parse(fetch())
            self._auth._verified = True
            self._auth.persist_verified_cookie()
            return parsed
        key = json.dumps(
            [self._url(path), method, kwargs.get("data"), kwargs.get("params")],
            sort_keys=True,
        )

        def on_hit() -> None:
            if self._on_progress is not None:
                self._on_progress(f"checkpoint hit {method} {path}")

        def verified_parse(text):
            parsed = parse(text)
            # Cached bodies cannot verify the current live Cookie jar.
            if self._last_request_succeeded:
                self._auth._verified = True
                self._auth.persist_verified_cookie()
            return parsed

        self._last_request_succeeded = False
        return self._checkpoint.read_or_fetch(key, fetch, verified_parse, on_hit)

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
            response = self._auth._read(method, path, refresh=self.recover_authentication, **kwargs)
        except (AuthenticationError, TransportError):
            self._last_request_succeeded = False
            raise

        self._last_request_succeeded = True
        if self._on_progress is not None:
            self._on_progress(
                f"{request_label} completed seconds={time.perf_counter() - started_at:.3f}"
            )
        return response

    def refresh_cookie(self) -> str:
        """Verify first; recover CAS once and open a browser only when sign-in is required."""
        try:
            self._auth.check_authentication()
        except LoginRequired:
            self.relogin()
        cookie = self._auth._prepare("GET", "/").headers.get("Cookie", "")
        self.settings = replace(self.settings, cookie=cookie)
        return cookie

    def relogin(self, *, fresh: bool = False) -> None:
        """Force browser authentication, reusing the saved CAS session when valid."""
        if fresh:
            browser_login(self.settings, fresh=True)
        else:
            browser_login(self.settings)
        self._auth = TeachingSession(self.settings, session=self.session)

    @staticmethod
    def _json(response: requests.Response, stage: str) -> object:
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise ParseError(f"invalid JSON during {stage}") from exc

    def get_catalog(self) -> Catalog:
        return self._parsed_request("GET", "/zxjh/queryZxkc", parse_catalog_page)

    def get_student_profile(self) -> dict[str, str]:
        # Always live: an account identity must never come from a crawl checkpoint.
        profile = parse_student_profile(self._request("GET", "/xswhxx/queryXswhxx").text)
        # No filters or checkpoint: these defaults belong to the current account.
        response = self._request("GET", "/zxjh/queryZxkc")
        return parse_default_plan_identity(response.text, profile)

    def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
        def parse(text: str) -> tuple[Major, ...]:
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise ParseError("invalid JSON during major list") from exc
            return parse_major_list(payload, year, department)

        return self._parsed_request(
            "POST",
            "/pub/queryYxzyList_x",
            parse,
            data={"yxdm": department.code, "nj": year},
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )

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
        first_page: PlanPage = self._parsed_request(
            "POST", "/zxjh/queryZxkc", parse_plan_page, data=filters, headers=headers
        )
        if first_page.page_no != 1:
            raise ParseError("execution-plan first page number is not 1")
        all_courses.extend(first_page.courses)
        effective_page_size = first_page.page_size or page_size

        for page_no in range(2, first_page.page_count + 1):
            parsed = self._parsed_request(
                "POST",
                "/zxjh/queryZxkc",
                parse_plan_page,
                data={
                    **filters,
                    "pageNo": str(page_no),
                    "pageSize": str(effective_page_size),
                    "pageCount": str(first_page.page_count),
                },
                headers=headers,
            )
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
            introduction = self._parsed_request(
                "GET",
                "/pub/queryKcxxView",
                lambda text, course_code=course_code: parse_course_introduction(text, course_code),
                params={"kcdm": course_code},
            )
            introductions[course_code] = {"default": introduction}
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
        self._auth._verified = True
        self._auth.persist_verified_cookie()
        return all_grades
