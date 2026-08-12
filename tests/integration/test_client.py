from urllib.parse import parse_qs

import pytest
import requests
import responses
from responses import matchers

from hoahrb_jwts.client import TeachingSystemClient
from hoahrb_jwts.config import Settings
from hoahrb_jwts.errors import AuthenticationError, TransportError
from hoahrb_jwts.models import Major
from tests.helpers import load_fixture


def _grade_list_html(
    detail_id: str, task_id: str, plan_id: str, *, page_count: int = 1, page_size: int = 20
) -> str:
    return f"""
    <form method='post' action='/cjcx/queryQmcj'>
      <input name='pageNo' /><input name='pageSize' value='{page_size}' />
      <input name='pageCount' value='{page_count}' />
    </form>
    <div class='cz_button cz_see'
         onclick=\"queryCjView('{detail_id}', '{task_id}', '{plan_id}')\"></div>
    """


def _grade_detail_html(course_code: str, components: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"<tr><th>{name}：</th><td>权重（占总成绩百分比）[{percent}]，满分[100.0]</td>"
        "<th>本项得分</th><td>90</td></tr>"
        for name, percent in components
    )
    return f"<table><tr><th>课程代码：</th><td>{course_code}</td></tr>{rows}</table>"


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
    assert responses.calls[0].request.headers["User-Agent"].startswith("Mozilla/5.0")
    assert "maintainer-crawler" not in responses.calls[0].request.headers["User-Agent"]
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
    assert client.settings.cookie == refreshed_cookie
    assert responses.calls[1].request.headers["Cookie"] == refreshed_cookie


@responses.activate
def test_refresh_cookie_ignores_empty_cookie_segments() -> None:
    settings = Settings.from_env(
        {
            "HIT_JW_COOKIE": "JSESSIONID=old; CAMPUS=preserved;",
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

    refreshed_cookie = TeachingSystemClient(settings).refresh_cookie()

    assert refreshed_cookie == "JSESSIONID=new; CAMPUS=preserved"


@responses.activate
def test_refresh_cookie_keeps_current_cookie_when_login_does_not_update_it(settings) -> None:
    responses.get("http://jwts.hit.edu.cn/loginCAS", body="session page", status=200)
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("catalog_authenticated.html"),
        status=200,
    )

    client = TeachingSystemClient(settings)
    assert client.refresh_cookie() == settings.cookie
    client.get_catalog()
    assert responses.calls[1].request.headers["Cookie"] == settings.cookie


@responses.activate
def test_business_request_rejects_login_page_after_cookie_probe(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        body="<title>统一身份认证</title>",
        status=200,
        headers={"Set-Cookie": "JSESSIONID=not-authenticated; Path=/"},
    )
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("catalog_login.html"),
        status=200,
    )

    client = TeachingSystemClient(settings)
    client.refresh_cookie()
    with pytest.raises(AuthenticationError, match="not authenticated"):
        client.get_catalog()


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
def test_refresh_cookie_accepts_redirect_without_cookie_update(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        status=302,
        headers={"Location": "http://ids.hit.edu.cn/authserver/login"},
    )

    assert TeachingSystemClient(settings).refresh_cookie() == settings.cookie


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
    from hoahrb_jwts.models import Department

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
    from hoahrb_jwts.models import Department

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


@responses.activate
def test_get_course_introductions_requests_sorted_unique_course_codes(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/pub/queryKcxxView",
        body=load_fixture("course_detail_empty.html"),
        status=200,
        match=[matchers.query_param_matcher({"kcdm": "22EMPTY001"})],
    )
    responses.get(
        "http://jwts.hit.edu.cn/pub/queryKcxxView",
        body=load_fixture("course_detail_bilingual.html"),
        status=200,
        match=[matchers.query_param_matcher({"kcdm": "22MA15005"})],
    )

    result = TeachingSystemClient(settings).get_course_introductions(
        ["22MA15005", "22EMPTY001", "22MA15005"]
    )

    assert result == {
        "22EMPTY001": {"default": {"zh": "", "en": ""}},
        "22MA15005": {
            "default": {
                "zh": "课程包含极限、 一元微分学。",
                "en": "This course covers limits and calculus.",
            }
        },
    }
    assert [call.request.path_url for call in responses.calls] == [
        "/pub/queryKcxxView?kcdm=22EMPTY001",
        "/pub/queryKcxxView?kcdm=22MA15005",
    ]


@responses.activate
def test_get_grade_summary_follows_query_qmcj_detail_button(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryQmcj",
        body=_grade_list_html("detail-1", "task-1", "plan-1"),
        status=200,
    )
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryCjxxView",
        body=_grade_detail_html("C001", [("作业", "40.0%"), ("考试", "60.0%")]),
        status=200,
    )

    assert TeachingSystemClient(settings).get_grade_summary() == {
        "C001": {
            "default": [
                {"name": "作业", "percent": "40%"},
                {"name": "考试", "percent": "60%"},
            ]
        }
    }


@responses.activate
def test_get_grade_summary_falls_back_to_post_form(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryQmcj",
        body="<form><input name='course' /></form>",
        status=200,
    )
    responses.post(
        "http://jwts.hit.edu.cn/cjcx/queryQmcj",
        body=_grade_list_html("detail-1", "task-1", "plan-1"),
        status=200,
    )
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryCjxxView",
        body=_grade_detail_html("C001", [("考试", "100.0%")]),
        status=200,
    )

    summary = TeachingSystemClient(settings).get_grade_summary()

    assert summary == {"C001": {"default": [{"name": "考试", "percent": "100%"}]}}
    assert parse_qs(responses.calls[1].request.body, keep_blank_values=True) == {
        "pageSize": ["500"]
    }


@responses.activate
def test_get_grade_summary_posts_later_query_qmcj_pages(settings) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryQmcj",
        body=_grade_list_html("detail-1", "task-1", "plan-1", page_count=2),
        status=200,
    )
    responses.post(
        "http://jwts.hit.edu.cn/cjcx/queryQmcj",
        body=_grade_list_html("detail-2", "task-2", "plan-2", page_count=2),
        status=200,
    )
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryCjxxView",
        body=_grade_detail_html("C001", [("作业", "30.0%"), ("考试", "70.0%")]),
        status=200,
    )
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryCjxxView",
        body=_grade_detail_html("C002", [("报告", "100.0%")]),
        status=200,
    )

    summary = TeachingSystemClient(settings).get_grade_summary()

    assert summary == {
        "C001": {
            "default": [
                {"name": "作业", "percent": "30%"},
                {"name": "考试", "percent": "70%"},
            ]
        },
        "C002": {"default": [{"name": "报告", "percent": "100%"}]},
    }
    assert parse_qs(responses.calls[1].request.body, keep_blank_values=True) == {
        "pageNo": ["2"],
        "pageSize": ["20"],
        "pageCount": ["2"],
    }
