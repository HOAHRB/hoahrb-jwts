import importlib.util
import io
from pathlib import Path
from unittest.mock import patch


def _load_script():
    script_path = Path(__file__).parents[2] / "scripts" / "update_grades_summary.py"
    spec = importlib.util.spec_from_file_location("update_grades_summary", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_update_grades_summary_defaults_to_current_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    script = _load_script()
    source = (
        '[grades.C001]\ncourse_name = "Course"\n"24级" = { grade = "作业 40% + 考试 60%" }\n'
    ).encode()

    monkeypatch.chdir(tmp_path)
    with patch.object(script.urllib.request, "urlopen", return_value=io.BytesIO(source)):
        script.main([])

    assert (tmp_path / "grades_summary.json").read_text(encoding="utf-8") == (
        '{\n  "C001": {\n    "2024_default": [\n      {\n        "name": "\u4f5c\u4e1a",\n'
        '        "percent": "40%"\n      },\n      {\n        "name": "\u8003\u8bd5",\n'
        '        "percent": "60%"\n      }\n    ]\n  }\n}\n'
    )
