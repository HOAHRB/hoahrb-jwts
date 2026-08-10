import json
from pathlib import Path

import responses

from hoahrb_jwts.app import main


@responses.activate
def test_grades_command_publishes_grades_summary(tmp_path: Path) -> None:
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

    data_dir = tmp_path / "major-data"
    exit_code = main(
        ["crawl-grades", "--data-dir", str(data_dir)],
        environ={
            "HIT_JW_COOKIE": "JSESSIONID=sanitized",
            "HIT_JW_DELAY_SECONDS": "0",
            "HIT_JW_MAX_RETRIES": "0",
        },
    )

    assert exit_code == 0
    assert json.loads((data_dir / "grades_summary.json").read_text(encoding="utf-8")) == {
        "C001": {
            "default": [
                {"name": "作业", "percent": "40%"},
                {"name": "考试", "percent": "60%"},
            ]
        }
    }
