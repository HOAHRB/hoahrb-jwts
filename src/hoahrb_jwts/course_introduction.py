"""Parse HIT course-detail pages into the public introduction summary contract."""

from __future__ import annotations

from collections.abc import Mapping

from bs4 import BeautifulSoup

from .errors import AuthenticationError, ParseError

CourseIntroduction = dict[str, str]
CourseIntroductions = dict[str, dict[str, CourseIntroduction]]

_LOGIN_MARKERS = ("页面过期", "统一身份认证登录", "用户登录")


def _text(value: object) -> str:
    return " ".join(str(value).replace("\xa0", " ").split())


def _detail_pairs(soup: BeautifulSoup) -> Mapping[str, str]:
    pairs: dict[str, str] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for index in range(0, len(cells) - 1, 2):
            label = _text(cells[index].get_text(" ", strip=True)).rstrip(":：").strip()
            if label:
                pairs[label] = _text(cells[index + 1].get_text(" ", strip=True))
    return pairs


def parse_course_introduction(payload: object, expected_course_code: str) -> CourseIntroduction:
    """Extract bilingual introductions for one expected teaching-system course code."""

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str):
        raise ParseError("course-detail response is not HTML")
    if any(marker in payload for marker in _LOGIN_MARKERS):
        raise AuthenticationError("course-detail page is not authenticated")

    soup = BeautifulSoup(payload, "html.parser")
    page_text = _text(soup.get_text(" ", strip=True))
    if any(marker in page_text for marker in _LOGIN_MARKERS):
        raise AuthenticationError("course-detail page is not authenticated")
    pairs = _detail_pairs(soup)
    if not {"课程代码", "课程简介", "课程英文简介"}.issubset(pairs):
        raise ParseError("course-detail response has no recognizable fields")
    if pairs["课程代码"] != expected_course_code:
        raise ParseError("course-detail course code does not match request")
    return {"zh": pairs["课程简介"], "en": pairs["课程英文简介"]}
