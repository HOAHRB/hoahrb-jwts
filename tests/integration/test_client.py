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
def test_refresh_checks_valid_cookie_without_cas(settings):
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc", body=load_fixture("catalog_authenticated.html")
    )
    client = TeachingSystemClient(settings)
    assert client.refresh_cookie() == settings.cookie
    assert len(responses.calls) == 1


@responses.activate
def test_expired_session_recovers_cas_then_replays_query(settings):
    from dataclasses import replace

    settings = replace(settings, cas_cookie="TGC=existing")
    responses.get("http://jwts.hit.edu.cn/zxjh/queryZxkc", status=401)
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        status=302,
        headers={"Location": "https://ids.hit.edu.cn/authserver/login?service=private"},
    )
    responses.get(
        "https://ids.hit.edu.cn/authserver/login?service=private",
        status=302,
        headers={
            "Location": "http://jwts.hit.edu.cn/callback?ticket=private",
            "Set-Cookie": "TGC=rotated; Secure; Path=/",
        },
    )
    responses.get(
        "http://jwts.hit.edu.cn/callback?ticket=private",
        status=302,
        headers={"Location": "/main", "Set-Cookie": "JSESSIONID=new; Path=/"},
    )
    responses.get("http://jwts.hit.edu.cn/main", body="ready")
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc", body=load_fixture("catalog_authenticated.html")
    )
    client = TeachingSystemClient(settings)
    assert client.get_catalog().years == ("2025", "2024")
    cas = [c for c in responses.calls if "ids.hit.edu.cn" in c.request.url][0]
    assert cas.request.headers.get("Cookie") == "TGC=existing"
    for call in responses.calls:
        if "jwts.hit.edu.cn" in call.request.url:
            assert "TGC=" not in call.request.headers.get("Cookie", "")


@responses.activate
def test_failed_login_does_not_write_cookie(settings, tmp_path):
    from dataclasses import replace

    from hoahrb_jwts.config import CookieSource
    from hoahrb_jwts.errors import LoginRequired

    path = tmp_path / ".env"
    path.write_text('HIT_JW_COOKIE="JSESSIONID=sanitized"', encoding="utf-8")
    settings = replace(
        settings, cookie_source=CookieSource.DOTENV, cookie_file=path.with_name(".env.cookies.json")
    )
    responses.get("http://jwts.hit.edu.cn/zxjh/queryZxkc", body=load_fixture("catalog_login.html"))
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        body='<input type="password">',
        headers={"Set-Cookie": "JSESSIONID=unverified; Path=/"},
    )
    with pytest.raises(LoginRequired):
        TeachingSystemClient(settings).get_catalog()
    assert "JSESSIONID=sanitized" in path.read_text()
    assert not path.with_name(".env.cookies.json").exists()


@responses.activate
def test_startup_login_opens_firefox_only_for_login_required(settings, monkeypatch):
    from hoahrb_jwts.errors import LoginRequired

    responses.get("http://jwts.hit.edu.cn/zxjh/queryZxkc", status=401)
    responses.get("http://jwts.hit.edu.cn/loginCAS", body='<input type="password">')
    calls = []

    def login(_settings):
        calls.append("browser")
        raise LoginRequired("cancelled")

    monkeypatch.setattr("hoahrb_jwts.client.browser_login", login)
    with pytest.raises(LoginRequired):
        TeachingSystemClient(settings).refresh_cookie()
    assert calls == ["browser"]


@responses.activate
def test_refresh_transport_failure_remains_transport_failure(settings):
    def fail(_request):
        raise requests.Timeout("private")

    responses.add_callback(responses.GET, "http://jwts.hit.edu.cn/zxjh/queryZxkc", callback=fail)
    with pytest.raises(TransportError):
        TeachingSystemClient(settings).refresh_cookie()
    assert len(responses.calls) == 1


@responses.activate
def test_untrusted_redirect_rejected_without_leaking_ticket(settings):
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        status=302,
        headers={"Location": "https://evil.example/?ticket=private"},
    )
    with pytest.raises(AuthenticationError) as error:
        TeachingSystemClient(settings).get_catalog()
    assert "private" not in str(error.value)
    assert len(responses.calls) == 1


@responses.activate
def test_business_post_redirect_is_not_parsed_as_json(settings):
    from hoahrb_jwts.models import Department

    responses.post(
        "http://jwts.hit.edu.cn/pub/queryYxzyList_x",
        body='[{"pageZydm":"35158","pageZymc":"俄语"}]',
        status=302,
        headers={"Location": "/loginCAS"},
    )
    client = TeachingSystemClient(settings)
    client.recover_authentication = False
    with pytest.raises(AuthenticationError):
        client.get_majors("2025", Department("35", "人文社科学部"))
    assert len(responses.calls) == 1


@responses.activate
def test_verified_rotation_is_persisted_and_restored(settings, tmp_path):
    from dataclasses import replace

    from hoahrb_jwts.config import CookieSource

    path = tmp_path / ".env"
    path.write_bytes(b'\xef\xbb\xbf# keep\r\nHIT_JW_COOKIE="JSESSIONID=sanitized" # keep\r\n')
    settings = replace(
        settings, cookie_source=CookieSource.DOTENV, cookie_file=path.with_name(".env.cookies.json")
    )
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("catalog_authenticated.html"),
        headers=[
            ("Set-Cookie", "JSESSIONID=new; Path=/"),
            ("Set-Cookie", "scoped=value; Path=/pub"),
        ],
    )
    TeachingSystemClient(settings).get_catalog()
    updated = Settings.from_sources({}, path)
    client = TeachingSystemClient(updated)
    assert "scoped=value" in client._auth._prepare("GET", "/pub/test").headers.get("Cookie", "")
    assert "scoped=" not in client._auth._prepare("GET", "/").headers.get("Cookie", "")
    assert (
        path.read_bytes()
        == b'\xef\xbb\xbf# keep\r\nHIT_JW_COOKIE="JSESSIONID=sanitized" # keep\r\n'
    )


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


def test_saved_session_can_be_loaded_into_firefox(settings):
    from dataclasses import replace

    from hoahrb_jwts.auth_session import TeachingSession
    from hoahrb_jwts.browser_login import export_browser_cookies

    with TeachingSession(replace(settings, cas_cookie="TGC=remembered")) as session:
        cookies = export_browser_cookies(session)
    cas = next(item for item in cookies if item["name"] == "TGC")
    assert cas["value"] == "remembered"
    assert cas["domain"] == "ids.hit.edu.cn"
    assert cas["secure"] is True


def test_login_browser_fallback_and_native_user_agent():
    from unittest.mock import MagicMock

    from playwright.sync_api import Error

    from hoahrb_jwts.browser_login import launch_login_browser

    runtime = MagicMock()
    browser = launch_login_browser(runtime, {"headless": False})
    assert browser is runtime.firefox.launch.return_value
    runtime.chromium.launch.assert_not_called()
    runtime.firefox.launch.side_effect = Error("not installed")
    assert (
        launch_login_browser(runtime, {"headless": False}) is runtime.chromium.launch.return_value
    )
    runtime.webkit.launch.assert_not_called()
    runtime.chromium.launch.side_effect = Error("not installed")
    assert launch_login_browser(runtime, {"headless": False}) is runtime.webkit.launch.return_value
