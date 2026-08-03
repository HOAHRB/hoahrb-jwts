from urllib.parse import parse_qs

import pytest
import requests
import responses

from hoa_cli.client import TeachingSystemClient
from hoa_cli.config import Settings
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
def test_refresh_cookie_merges_update_and_uses_it_for_later_requests() -> None:
    settings = Settings.from_env(
        {
            "HIT_JW_COOKIE": "JSESSIONID=old; CAMPUS=preserved",
            "HIT_JW_DELAY_SECONDS": "0",
            "HIT_JW_MAX_RETRIES": "0",
        }
    )
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        body="refreshed session",
        status=200,
        headers={"Set-Cookie": "JSESSIONID=new; Path=/; HttpOnly"},
    )
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("catalog_authenticated.html"),
        status=200,
    )
    client = TeachingSystemClient(settings)

    refreshed_cookie = client.refresh_cookie()
    client.get_catalog()

    assert refreshed_cookie == "JSESSIONID=new; CAMPUS=preserved"
    assert responses.calls[1].request.headers["Cookie"] == refreshed_cookie


@responses.activate
def test_refresh_cookie_rejects_missing_cookie_update(settings) -> None:
    responses.get("http://jwts.hit.edu.cn/loginCAS", body="session page", status=200)

    with pytest.raises(AuthenticationError, match="no usable Cookie update"):
        TeachingSystemClient(settings).refresh_cookie()


@responses.activate
def test_refresh_cookie_rejects_login_page_even_with_cookie_update(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        body="<title>统一身份认证</title>",
        status=200,
        headers={"Set-Cookie": "JSESSIONID=not-authenticated; Path=/"},
    )

    with pytest.raises(AuthenticationError, match="login page"):
        TeachingSystemClient(settings).refresh_cookie()


@responses.activate
def test_refresh_cookie_accepts_redirect_with_cookie_update(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        status=302,
        headers={
            "Location": "http://jwts.hit.edu.cn/zxjh/queryZxkc",
            "Set-Cookie": "JSESSIONID=redirect-refreshed; Path=/; HttpOnly",
        },
    )

    refreshed_cookie = TeachingSystemClient(settings).refresh_cookie()

    assert refreshed_cookie == "JSESSIONID=redirect-refreshed"


@responses.activate
def test_refresh_cookie_rejects_redirect_without_cookie_update(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        status=302,
        headers={"Location": "http://jwts.hit.edu.cn/zxjh/queryZxkc"},
    )

    with pytest.raises(AuthenticationError, match="no usable Cookie update"):
        TeachingSystemClient(settings).refresh_cookie()


@responses.activate
def test_refresh_cookie_maps_transport_failure_to_authentication_error(settings) -> None:
    def timeout_callback(_request):
        raise requests.Timeout("timed out")

    responses.add_callback(
        responses.GET,
        "http://jwts.hit.edu.cn/loginCAS",
        callback=timeout_callback,
    )

    with pytest.raises(AuthenticationError, match="refresh request failed"):
        TeachingSystemClient(settings).refresh_cookie()


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
def test_get_majors_accepts_redirect_without_following_it(settings) -> None:
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

    majors = TeachingSystemClient(settings).get_majors("2025", Department("35", "人文社科学部"))

    assert len(responses.calls) == 1
    assert [major.code for major in majors] == ["35158"]


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
