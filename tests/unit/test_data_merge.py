import json

import pytest
import toml

from hoahrb_jwts.data_merge import merge_data, parse_default_plan_identity, parse_student_profile
from hoahrb_jwts.errors import ParseError, ValidationError
from hoahrb_jwts.writer import publish_plans
from tests.unit.test_writer import plan


def test_merge_fresh_crawl_preserves_remote_grades_and_other_plans(tmp_path):
    source, target = tmp_path / "local", tmp_path / "checkout"
    publish_plans(source, (plan(name="Renamed"),), {"2025"})
    publish_plans(target, (plan(), plan(code="OTHER")), {"2025"})
    graded = next(
        p
        for p in (target / "plans").glob("*.toml")
        if toml.load(p)["info"]["major_code"] == "35158"
    )
    data = toml.load(graded)
    data["courses"][0]["grade_details"] = [{"name": "Exam", "percent": "100%"}]
    graded.write_text(toml.dumps(data), encoding="utf-8")
    other = next(p for p in (target / "plans").glob("*.toml") if p != graded)
    before_other = other.read_bytes()
    (target / "grades_summary.json").write_text('{"repository":{}}', encoding="utf-8")
    result = merge_data(source, target)
    assert result.updated == 1
    assert other.read_bytes() == before_other
    assert not graded.exists()
    updated = next(p for p in (target / "plans").glob("*.toml") if p != other)
    assert toml.load(updated)["courses"][0]["grade_details"] == data["courses"][0]["grade_details"]
    assert (target / "grades_summary.json").read_text() == '{"repository":{}}'
    before = {p.name: p.read_bytes() for p in (target / "plans").glob("*.toml")}
    merge_data(source, target)
    assert {p.name: p.read_bytes() for p in (target / "plans").glob("*.toml")} == before


def test_unresolved_contribution_does_not_modify_checkout(tmp_path):
    source, target = tmp_path / "local", tmp_path / "checkout"
    publish_plans(source, (plan(name="Renamed"),), {"2025"})
    publish_plans(target, (plan(),), {"2025"})
    bundles = source / "grade_contributions"
    bundles.mkdir()
    (bundles / "pending.json").write_text(json.dumps({"profile": {"year": "2025"}, "grades": {}}))
    before = {p.name: p.read_bytes() for p in (target / "plans").glob("*.toml")}
    with pytest.raises(ValidationError, match="unresolved account"):
        merge_data(source, target)
    assert {p.name: p.read_bytes() for p in (target / "plans").glob("*.toml")} == before


def test_profile_excludes_personal_fields_and_requires_identity():
    html = "<table><tr><th>姓名：</th><td>Private</td><th>年级：</th><td>2025</td><th>专业：</th><td>俄语</td><th>学院：</th><td>学院</td></tr></table>"
    assert parse_student_profile(html) == {
        "year": "2025",
        "major_name": "俄语",
        "school_name": "学院",
    }
    with pytest.raises(ParseError):
        parse_student_profile("<table><th>年级：</th><td>2025</td></table>")


def test_account_defaults_resolve_same_name_codes_without_guessing():
    html = """<select name="pageNj"><option selected value="2025">2025</option></select>
    <select name="pageYxdm"><option selected value="35">[35]School</option></select>
    <select name="pageZydm"><option value="35152">Russian</option>
    <option selected value="35158">Russian</option></select>"""
    profile = {"year": "2025", "school_name": "School", "major_name": "Russian"}
    assert parse_default_plan_identity(html, profile)["major_code"] == "35158"
    with pytest.raises(ParseError, match="disagrees"):
        parse_default_plan_identity(html, {**profile, "year": "2024"})
    with pytest.raises(ParseError, match="unique selected"):
        parse_default_plan_identity(
            html.replace('selected value="35158"', 'value="35158"'), profile
        )


def test_grade_contribution_scoped_and_conflict_preserved(tmp_path):
    source, target = tmp_path / "local", tmp_path / "checkout"
    publish_plans(target, (plan(), plan(code="OTHER")), {"2025"})
    bundles = source / "grade_contributions"
    bundles.mkdir(parents=True)
    bundle = {
        "profile": {"year": "2025", "major_code": "35158"},
        "grades": {"C001": {"default": [{"name": "Exam", "percent": "100%"}]}},
    }
    (bundles / "one.json").write_text(json.dumps(bundle), encoding="utf-8")
    merge_data(source, target)
    for path in (target / "plans").glob("*.toml"):
        data = toml.load(path)
        if data["info"]["major_code"] == "35158":
            assert data["courses"][0]["grade_details"] == bundle["grades"]["C001"]["default"]
        else:
            assert "grade_details" not in data["courses"][0]
    bundle["grades"]["C001"]["default"] = [{"name": "Different"}]
    (bundles / "two.json").write_text(json.dumps(bundle), encoding="utf-8")
    result = merge_data(source, target)
    assert result.conflicts == ("2025/35158/C001",)
    assert (target / "grade_contributions/two.json").read_bytes() == (
        bundles / "two.json"
    ).read_bytes()
    assert list((target / "grade_conflicts").glob("*.json"))


def test_merge_rolls_back_on_write_failure(tmp_path, monkeypatch):
    import os

    from hoahrb_jwts.data_merge import write_files
    from hoahrb_jwts.errors import PublicationError

    root = tmp_path / "checkout"
    root.mkdir()
    (root / "a.json").write_bytes(b"old a")
    (root / "b.json").write_bytes(b"old b")
    replace = os.replace

    def fail_second(src, dest):
        if dest.name == "b.json":
            raise OSError("simulated failure")
        return replace(src, dest)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(PublicationError, match="restored"):
        write_files(root, {"a.json": b"new a", "b.json": b"new b"})
    assert (root / "a.json").read_bytes() == b"old a"
    assert (root / "b.json").read_bytes() == b"old b"
