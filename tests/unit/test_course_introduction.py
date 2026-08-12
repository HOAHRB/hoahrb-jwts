import pytest

from hoahrb_jwts.course_introduction import parse_course_introduction
from hoahrb_jwts.errors import AuthenticationError, ParseError
from tests.helpers import load_fixture


def test_parse_course_introduction_extracts_bilingual_text() -> None:
    assert parse_course_introduction(load_fixture("course_detail_bilingual.html"), "22MA15005") == {
        "zh": "课程包含极限、 一元微分学。",
        "en": "This course covers limits and calculus.",
    }


def test_parse_course_introduction_accepts_both_languages_empty() -> None:
    assert parse_course_introduction(load_fixture("course_detail_empty.html"), "22EMPTY001") == {
        "zh": "",
        "en": "",
    }


def test_parse_course_introduction_rejects_course_code_mismatch() -> None:
    with pytest.raises(ParseError, match="course code does not match"):
        parse_course_introduction(load_fixture("course_detail_bilingual.html"), "OTHER")


def test_parse_course_introduction_rejects_unrecognized_markup() -> None:
    with pytest.raises(ParseError, match="recognizable fields"):
        parse_course_introduction("<html><body>empty</body></html>", "22MA15005")


def test_parse_course_introduction_rejects_login_page() -> None:
    with pytest.raises(AuthenticationError, match="not authenticated"):
        parse_course_introduction("<title>统一身份认证登录</title>", "22MA15005")
