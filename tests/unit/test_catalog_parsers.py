import json

import pytest

from hoa_cli.errors import AuthenticationError, ParseError
from hoa_cli.models import Department
from hoa_cli.parsers import parse_catalog_page, parse_major_list
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
    assert (
        parse_major_list(
            json.loads(load_fixture("majors_empty.json")), "2025", Department("35", "人文社科学部")
        )
        == ()
    )


def test_parse_major_list_rejects_malformed_entries() -> None:
    with pytest.raises(ParseError):
        parse_major_list([{"pageZydm": "35158"}], "2025", Department("35", "人文社科学部"))
