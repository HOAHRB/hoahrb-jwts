from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from hoahrb_jwts import app
from hoahrb_jwts.errors import ValidationError
from tests.helpers import load_fixture


@pytest.mark.parametrize("failure", ["plan", "introduction", "publication"])
@pytest.mark.parametrize("fresh", [False, True])
@responses.activate
def test_crawl_resumes_after_failure_or_can_start_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, fresh: bool
) -> None:
    failed = False
    responses.get("http://jwts.hit.edu.cn/loginCAS", body="session")
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body=load_fixture("catalog_authenticated.html"),
    )

    def majors(request):
        params = parse_qs(request.body)
        body = '[{"pageZydm":"35158","pageZymc":"俄语【本】"}]'
        return 200, {}, body if params["yxdm"] == ["35"] else "[]"

    def plans(request):
        nonlocal failed
        page = parse_qs(request.body).get("pageNo", ["1"])[0]
        if failure == "plan" and page == "2" and not failed:
            failed = True
            return 503, {}, "unavailable"
        return (
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            load_fixture(f"plan_page_{page}.html"),
        )

    def introduction(request):
        nonlocal failed
        code = parse_qs(urlparse(request.url).query)["kcdm"][0]
        if failure == "introduction" and code == "22AD16004" and not failed:
            failed = True
            return 503, {}, "unavailable"
        return (
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            f"""<table>
            <tr><th>课程代码：</th><td>{code}</td></tr>
            <tr><th>课程简介：</th><td>简介</td></tr>
            <tr><th>课程英文简介：</th><td>Introduction</td></tr>
        </table>""",
        )

    responses.add_callback(responses.POST, "http://jwts.hit.edu.cn/pub/queryYxzyList_x", majors)
    responses.add_callback(responses.POST, "http://jwts.hit.edu.cn/zxjh/queryZxkc", plans)
    responses.add_callback(responses.GET, "http://jwts.hit.edu.cn/pub/queryKcxxView", introduction)
    publish = app.publish_plans

    def publish_once(*args, **kwargs):
        nonlocal failed
        if failure == "publication" and not failed:
            failed = True
            raise ValidationError("simulated publication validation failure")
        return publish(*args, **kwargs)

    monkeypatch.setattr(app, "publish_plans", publish_once)
    cache_dir = tmp_path / "cache"
    data_dir = tmp_path / "data"
    command = [
        "crawl",
        "--years",
        "2025",
        "--data-dir",
        str(data_dir),
        "--cache-dir",
        str(cache_dir),
    ]
    environ = {
        "HIT_JW_COOKIE": "JSESSIONID=sanitized",
        "HIT_JW_DELAY_SECONDS": "0",
        "HIT_JW_MAX_RETRIES": "0",
    }
    assert app.main(command, environ) == (4 if failure == "publication" else 3)
    assert failed
    cached_files = list(cache_dir.rglob("*.json"))
    assert cached_files
    assert all("JSESSIONID" not in file.read_text(encoding="utf-8") for file in cached_files)
    assert not (data_dir / "major_mapping.json").exists()

    previous_count = len(responses.calls)
    assert app.main(command + (["--fresh"] if fresh else []), environ) == 0
    resumed = responses.calls[previous_count:]
    paths = [urlparse(call.request.url).path for call in resumed]
    assert paths.count("/loginCAS") == 0
    assert paths.count("/pub/queryYxzyList_x") == (2 if fresh else int(failure == "plan"))
    assert paths.count("/zxjh/queryZxkc") == 1 + (3 if fresh else int(failure == "plan"))
    assert paths.count("/pub/queryKcxxView") == (
        3 if fresh or failure == "plan" else 2 if failure == "introduction" else 0
    )
    assert (data_dir / "major_mapping.json").exists()
    assert (data_dir / "course_introductions.json").exists()
    assert not list(cache_dir.rglob("*.json"))
