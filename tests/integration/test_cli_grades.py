import json
from pathlib import Path

import responses
import toml

from hoahrb_jwts.app import main
from hoahrb_jwts.writer import publish_plans
from tests.helpers import load_fixture
from tests.unit.test_writer import plan


@responses.activate
def test_grades_command_publishes_only_account_plan_details(tmp_path: Path) -> None:
    responses.get(
        "http://jwts.hit.edu.cn/loginCAS",
        body="refreshed session",
        status=200,
        headers={"Set-Cookie": "JSESSIONID=refreshed; Path=/"},
    )
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryQmcj",
        body="""
        <div class='cz_button cz_see'
             onclick=\"queryCjView('detail-1', 'task-1', 'plan-1')\"></div>
        """,
        status=200,
    )
    responses.get(
        "http://jwts.hit.edu.cn/cjcx/queryCjxxView",
        body="""
        <table>
          <tr><th>课程代码：</th><td>C001</td></tr>
          <tr><th>作业：</th><td>权重（占总成绩百分比）[40.0%]，满分[100.0]</td></tr>
          <tr><th>考试：</th><td>权重（占总成绩百分比）[60.0%]，满分[100.0]</td></tr>
        </table>
        """,
        status=200,
    )

    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc", body=load_fixture("catalog_authenticated.html")
    )
    data_dir = tmp_path / "major-data"
    publish_plans(data_dir, (plan(), plan(code="OTHER")), {"2025"})
    responses.get(
        "http://jwts.hit.edu.cn/xswhxx/queryXswhxx",
        body="""
        <table><tr><th>年级：</th><td>2025</td><th>专业：</th><td>俄语</td>
        <th>学院：</th><td>人文社科学部</td><th>专业代码：</th><td>35158</td></tr></table>""",
    )
    responses.get(
        "http://jwts.hit.edu.cn/zxjh/queryZxkc",
        body="""
      <select name="pageNj"><option selected value="2025">2025</option></select>
      <select name="pageYxdm"><option selected value="35">[35]人文社科学部</option></select>
      <select name="pageZydm"><option value="35152">俄语</option>
      <option selected value="35158">俄语</option></select>""",
    )
    exit_code = main(
        ["crawl-grades", "--data-dir", str(data_dir)],
        environ={
            "HIT_JW_COOKIE": "JSESSIONID=sanitized",
            "HIT_JW_DELAY_SECONDS": "0",
            "HIT_JW_MAX_RETRIES": "0",
        },
    )

    assert exit_code == 0
    bundle = json.loads(
        next((data_dir / "grade_contributions").glob("*.json")).read_text(encoding="utf-8")
    )
    assert bundle["grades"] == {
        "C001": {
            "default": [
                {"name": "作业", "percent": "40%"},
                {"name": "考试", "percent": "60%"},
            ]
        }
    }
    for path in (data_dir / "plans").glob("*.toml"):
        data = toml.loads(path.read_text(encoding="utf-8"))
        if data["info"]["major_code"] == "35158":
            assert data["courses"][0]["grade_details"] == bundle["grades"]["C001"]["default"]
        else:
            assert "grade_details" not in data["courses"][0]
    assert not (data_dir / "grades_summary.json").exists()
