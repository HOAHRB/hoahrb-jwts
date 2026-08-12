import json

import pytest

from hoahrb_jwts.errors import AuthenticationError, ParseError
from hoahrb_jwts.models import Department
from hoahrb_jwts.parsers import parse_catalog_page, parse_major_list
from tests.helpers import load_fixture


def test_parse_catalog_preserves_historical_departments() -> None:
    catalog = parse_catalog_page(load_fixture("catalog_authenticated.html"))
    assert catalog.years == ("2025", "2024")
    assert catalog.departments == (
        Department(code="35", name="人文社科学部"),
        Department(code="08", name="人文与社会科学学院"),
    )


def test_parse_catalog_rejects_login_page() -> None:
    with pytest.raises(AuthenticationError):
        parse_catalog_page(load_fixture("catalog_login.html"))


def test_parse_major_list_is_scoped_to_year_and_department() -> None:
    majors = parse_major_list(
        json.loads(load_fixture("majors_2025_35.json")),
        "2025",
        Department(code="35", name="人文社科学部"),
    )
    assert [(m.year, m.department_code, m.code) for m in majors] == [
        ("2025", "35", "35158"),
        ("2025", "35", "35163"),
    ]
    assert majors[0].name == "俄语"
    assert majors[0].category == "本"
    assert (
        parse_major_list(
            json.loads(load_fixture("majors_empty.json")), "2025", Department("35", "人文社科学部")
        )
        == ()
    )


def test_parse_major_list_rejects_malformed_entries() -> None:
    with pytest.raises(ParseError):
        parse_major_list([{"pageZydm": "35158"}], "2025", Department("35", "人文社科学部"))


@pytest.mark.parametrize(
    ("code", "raw_name", "department_code", "expected"),
    [
        ("35F158", "俄语【辅修】", "35", "辅修"),
        ("35E158", "俄语【第二学士学位】", "35", "第二学士学位"),
        ("35M2501", "国际传播", "35", "微专业"),
        ("3525L01", "工科试验班", "35", "本"),
        ("35Y158", "俄语", "35", "Y"),
        ("35X158", "未知方案", "35", "未分类"),
    ],
)
def test_parse_major_list_classifies_explicit_and_unlabelled_majors(
    code: str, raw_name: str, department_code: str, expected: str
) -> None:
    major = parse_major_list(
        [{"pageZydm": code, "pageZymc": raw_name}],
        "2025",
        Department(department_code, "学院"),
    )[0]
    assert major.category == expected


def test_parse_major_list_y_marker_overrides_wrong_explicit_category() -> None:
    major = parse_major_list(
        [{"pageZydm": "09Y331", "pageZymc": "土木工程【本】"}],
        "2025",
        Department("09", "土木工程学院"),
    )[0]
    assert major.category == "Y"


def test_parse_major_list_overrides_known_01044_source_error() -> None:
    major = parse_major_list(
        [{"pageZydm": "01044", "pageZymc": "智能装备与系统（强基计划）【第二学士学位】"}],
        "2025",
        Department("01", "航天学院"),
    )[0]
    assert major.category == "本"


def test_parse_major_list_rejects_unknown_explicit_category() -> None:
    with pytest.raises(ParseError, match="unknown category"):
        parse_major_list(
            [{"pageZydm": "35158", "pageZymc": "俄语【新类别】"}],
            "2025",
            Department("35", "人文社科学部"),
        )
