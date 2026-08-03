from urllib.parse import parse_qs

import pytest
import requests
import responses

from hoa_cli.client import TeachingSystemClient
from hoa_cli.errors import AuthenticationError, TransportError
from hoa_cli.models import Major
from tests.helpers import load_fixture


@responses.activate
def test_get_catalog_sends_cookie_without_logging_it(settings, caplog) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("catalog_authenticated.html"),
        status=200,
    )
    catalog = TeachingSystemClient(settings).get_catalog()
    assert catalog.years == ("2025", "2024")
    assert responses.calls[0].request.headers["Cookie"] == "JSESSIONID=sanitized"
    assert "JSESSIONID=sanitized" not in caplog.text


@responses.activate
def test_get_catalog_rejects_login_page(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("catalog_login.html"),
        status=200,
    )
    with pytest.raises(AuthenticationError):
        TeachingSystemClient(settings).get_catalog()


@responses.activate
def test_get_catalog_rejects_redirect_without_following_or_exposing_secrets(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body="redirect body with Cookie: JSESSIONID=server-secret",
        status=302,
        headers={"Location": "http://jwts.hit.edu.cn/auth/login"},
    )
    responses.get(
        "http://jwts.hit.edu.cn/auth/login",
        body=load_fixture("catalog_authenticated.html"),
        status=200,
    )

    with pytest.raises(AuthenticationError) as error:
        TeachingSystemClient(settings).get_catalog()

    assert len(responses.calls) == 1
    assert "JSESSIONID=sanitized" not in str(error.value)
    assert "server-secret" not in str(error.value)


@responses.activate
def test_get_majors_rejects_redirect_without_parsing_body(settings) -> None:
    from hoa_cli.models import Department

    responses.post(
        "http://jwts.hit.edu.cn/pub/queryYxzyList_x",
        body='[{"pageZydm": "35158", "pageZymc": "俄语"}]',
        status=302,
        headers={"Location": "http://jwts.hit.edu.cn/auth/login"},
    )
    responses.get(
        "http://jwts.hit.edu.cn/auth/login",
        body=load_fixture("majors_2025_35.json"),
        status=200,
    )

    with pytest.raises(AuthenticationError):
        TeachingSystemClient(settings).get_majors("2025", Department("35", "人文社科学部"))

    assert len(responses.calls) == 1


@responses.activate
def test_transport_error_does_not_include_cookie(settings) -> None:
    def timeout_callback(_request):
        raise requests.Timeout("timed out")

    responses.add_callback(
        responses.GET,
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        callback=timeout_callback,
    )
    with pytest.raises(TransportError) as error:
        TeachingSystemClient(settings).get_catalog()
    assert "JSESSIONID=sanitized" not in str(error.value)


@responses.activate
def test_get_majors_sends_exact_cohort_department_form(settings) -> None:
    responses.post(
        "http://jwts.hit.edu.cn/pub/queryYxzyList_x",
        body=load_fixture("majors_2025_35.json"),
        status=200,
    )
    from hoa_cli.models import Department

    majors = TeachingSystemClient(settings).get_majors("2025", Department("35", "人文社科学部"))
    assert [major.code for major in majors] == ["35158", "35163"]
    assert parse_qs(responses.calls[0].request.body) == {"yxdm": ["35"], "nj": ["2025"]}


@responses.activate
def test_get_plan_posts_all_filters_and_combines_pages(settings) -> None:
    responses.post(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("plan_page_1.html"),
        status=200,
    )
    responses.post(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("plan_page_2.html"),
        status=200,
    )
    major = Major("2025", "35", "人文社科学部", "35158", "俄语")
    courses = TeachingSystemClient(settings).get_plan(major)
    assert [course.code for course in courses] == ["22AD11001", "22AD16004", "22FL22230"]
    assert [parse_qs(call.request.body, keep_blank_values=True) for call in responses.calls] == [
        {
            "pageNj": ["2025"],
            "pageYxdm": ["35"],
            "pageZydm": ["35158"],
            "pageKkxn1": [""],
            "pageKkxq1": [""],
            "pageKkxn": [""],
            "pageKkxq": [""],
            "pageKcmc": [""],
            "pageSize": ["500"],
        },
        {
            "pageNj": ["2025"],
            "pageYxdm": ["35"],
            "pageZydm": ["35158"],
            "pageKkxn1": [""],
            "pageKkxq1": [""],
            "pageKkxn": [""],
            "pageKkxq": [""],
            "pageKcmc": [""],
            "pageNo": ["2"],
            "pageSize": ["500"],
            "pageCount": ["2"],
        },
    ]
