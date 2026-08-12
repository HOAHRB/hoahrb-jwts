import json
from pathlib import Path

import pytest
import toml

from hoahrb_jwts.errors import PublicationError, ValidationError
from hoahrb_jwts.models import NormalizedCourse, NormalizedPlan
from hoahrb_jwts.writer import build_major_mapping, publish_plans


def plan(
    year: str = "2025",
    code: str = "35158",
    name: str = "俄语",
    school_name: str = "人文社科学部",
    department_code: str = "35",
    category: str = "本",
) -> NormalizedPlan:
    course = NormalizedCourse(
        course_code="C001",
        course_name="课程",
        credit=2.0,
        assessment_method="考查",
        recommended_year_semester="第一学年秋季",
        course_nature="必修",
        course_category="专业课",
        track=None,
        offering_college="学院",
        total_hours=32,
    )
    return NormalizedPlan(
        year,
        department_code,
        code,
        name,
        school_name,
        f"HIT-{year}-{code}",
        (course,),
        category,
    )


def test_build_major_mapping_is_sorted_and_compatible() -> None:
    mapping = build_major_mapping((plan(code="35163", name="汉语言文学"), plan()))
    assert list(mapping) == ["2025"]
    assert mapping["2025"]["35158"] == {
        "name": "俄语",
        "plan_ID": "HIT-2025-35158",
        "school_name": "人文社科学部",
        "majors": [],
    }


def test_publish_uses_parsed_plan_categories(tmp_path: Path) -> None:
    data_dir = tmp_path / "major-data"

    result = publish_plans(
        data_dir,
        (
            plan(year="2024", code="01041", name="自动化", department_code="01"),
            plan(year="2024", code="01E041", name="自动化", category="第二学士学位"),
            plan(year="2024", code="11BF304", name="自动化", category="辅修"),
            plan(year="2024", code="35M2501", name="国际传播", category="微专业"),
            plan(year="2024", code="35X001", name="未知方案", category="未分类"),
        ),
        {"2024"},
    )

    assert result.added == 5
    plans_dir = data_dir / "plans"
    assert (plans_dir / "本_2024_人文社科学部_自动化.toml").exists()
    assert (plans_dir / "第二学士学位_2024_人文社科学部_自动化.toml").exists()
    assert (plans_dir / "辅修_2024_人文社科学部_自动化.toml").exists()
    assert (plans_dir / "微专业_2024_人文社科学部_国际传播.toml").exists()
    assert (plans_dir / "未分类_2024_人文社科学部_未知方案.toml").exists()


def test_publish_keeps_same_name_plans_in_different_categories(tmp_path: Path) -> None:
    data_dir = tmp_path / "major-data"

    result = publish_plans(
        data_dir,
        (
            plan(year="2025", code="13BF031", name="计算机科学与技术", category="辅修"),
            plan(
                year="2025",
                code="13BY031",
                name="计算机科学与技术",
                department_code="13B",
                category="本科国际生",
            ),
        ),
        {"2025"},
    )

    assert result.added == 2
    plans_dir = data_dir / "plans"
    assert (plans_dir / "辅修_2025_人文社科学部_计算机科学与技术.toml").exists()
    assert (
        plans_dir / "本科国际生_2025_人文社科学部_计算机科学与技术_13BY031.toml"
    ).exists()


def test_publish_reports_both_codes_for_same_category_filename_collision(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match=r"01041.*01042"):
        publish_plans(
            tmp_path / "major-data",
            (
                plan(year="2024", code="01041", name="自动化"),
                plan(year="2024", code="01042", name="自动化"),
            ),
            {"2024"},
        )


def test_publish_rejects_unknown_plan_category(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unknown category"):
        publish_plans(
            tmp_path / "major-data",
            (plan(category="未知"),),
            {"2025"},
        )


def test_publish_includes_school_name_in_filename_to_separate_college_tracks(
    tmp_path: Path,
) -> None:
    plans_dir = tmp_path / "major-data" / "plans"

    result = publish_plans(
        tmp_path / "major-data",
        (
            plan(
                year="2024", code="0223L080", name="机器人与智能装备类", school_name="机电工程学院"
            ),
            plan(
                year="2024",
                code="0423L080",
                name="机器人与智能装备类",
                school_name="能源科学与工程学院",
            ),
        ),
        {"2024"},
    )

    assert result.added == 2
    assert (plans_dir / "本_2024_机电工程学院_机器人与智能装备类.toml").exists()
    assert (plans_dir / "本_2024_能源科学与工程学院_机器人与智能装备类.toml").exists()


def test_publish_omits_repeated_school_and_major_name_from_filename(tmp_path: Path) -> None:
    data_dir = tmp_path / "major-data"

    publish_plans(
        data_dir,
        (plan(year="2024", name="建筑学院", school_name="建筑学院"),),
        {"2024"},
    )

    assert (data_dir / "plans" / "本_2024_建筑学院.toml").exists()


def test_publish_replaces_only_requested_generated_year_and_preserves_curated_files(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "major-data"
    plans_dir = data_dir / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "2024_本_旧专业.toml").write_text("old 2024", encoding="utf-8")
    (plans_dir / "2025_本_旧专业.toml").write_text("obsolete", encoding="utf-8")
    (data_dir / "major_mapping.json").write_text(
        json.dumps(
            {
                "2024": {"old": {"name": "旧专业"}},
                "2025": {"old": {"name": "旧专业"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    curated = {
        "lookup_table.toml": "lookup",
        "grades_summary.json": "grades",
        "shared_categories.toml": "categories",
    }
    for name, value in curated.items():
        (data_dir / name).write_text(value, encoding="utf-8")

    result = publish_plans(data_dir, (plan(name="俄/语:类?"),), {"2025"})
    assert result.added == 1
    assert not (plans_dir / "2025_本_旧专业.toml").exists()
    assert (plans_dir / "2024_本_旧专业.toml").read_text(encoding="utf-8") == "old 2024"
    generated = plans_dir / "本_2025_人文社科学部_俄-语-类-.toml"
    assert generated.exists()
    assert toml.load(generated)["info"]["major_name"] == "俄/语:类?"
    mapping = json.loads((data_dir / "major_mapping.json").read_text(encoding="utf-8"))
    assert "old" in mapping["2024"]
    assert list(mapping["2025"]) == ["35158"]
    for name, value in curated.items():
        assert (data_dir / name).read_text(encoding="utf-8") == value


def test_publish_rolls_back_when_replacement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "major-data"
    plans_dir = data_dir / "plans"
    plans_dir.mkdir(parents=True)
    original_plan = plans_dir / "2025_本_旧专业.toml"
    original_plan.write_text("original plan", encoding="utf-8")
    original_mapping = data_dir / "major_mapping.json"
    original_mapping.write_text('{"2025": {"old": {"name": "旧专业"}}}\n', encoding="utf-8")
    original_plan_bytes = original_plan.read_bytes()
    original_mapping_bytes = original_mapping.read_bytes()
    real_replace = __import__("os").replace
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr("hoahrb_jwts.writer.os.replace", fail_once)
    with pytest.raises(PublicationError):
        publish_plans(data_dir, (plan(),), {"2025"})
    assert original_plan.read_bytes() == original_plan_bytes
    assert original_mapping.read_bytes() == original_mapping_bytes


def test_publish_preserves_backup_when_rollback_restore_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "major-data"
    plans_dir = data_dir / "plans"
    plans_dir.mkdir(parents=True)
    original_plan = plans_dir / "2025_本_旧专业.toml"
    original_plan.write_text("original plan", encoding="utf-8")
    original_mapping = data_dir / "major_mapping.json"
    original_mapping.write_text('{"2025": {"old": {"name": "旧专业"}}}\n', encoding="utf-8")
    original_plan_bytes = original_plan.read_bytes()
    original_mapping_bytes = original_mapping.read_bytes()
    real_replace = __import__("os").replace
    calls = 0

    def fail_publish_then_restore_plans(source, destination):
        nonlocal calls
        calls += 1
        if calls in {4, 5}:
            raise OSError("injected publication or restore failure")
        return real_replace(source, destination)

    monkeypatch.setattr("hoahrb_jwts.writer.os.replace", fail_publish_then_restore_plans)
    with pytest.raises(PublicationError) as error:
        publish_plans(data_dir, (plan(),), {"2025"})

    assert (
        str(error.value) == "publication failed and rollback was incomplete; backup was preserved"
    )
    backup_roots = list(tmp_path.glob(".hoahrb-jwts-backup-*"))
    assert len(backup_roots) == 1
    assert (backup_roots[0] / "plans" / original_plan.name).read_bytes() == original_plan_bytes
    assert (backup_roots[0] / "major_mapping.json").read_bytes() == original_mapping_bytes


def test_publish_rejects_plans_outside_requested_year(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        publish_plans(tmp_path / "data", (plan(year="2024"),), {"2025"})


def test_publish_incrementally_updates_involved_course_introductions(tmp_path: Path) -> None:
    data_dir = tmp_path / "major-data"
    data_dir.mkdir()
    (data_dir / "course_introductions.json").write_text(
        json.dumps(
            {
                "C001": {"default": {"zh": "旧简介", "en": "old"}},
                "PRESERVED": {"default": {"zh": "保留", "en": "preserved"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = publish_plans(
        data_dir,
        (plan(),),
        {"2025"},
        {"C001": {"default": {"zh": "", "en": "new"}}},
    )

    introductions = json.loads((data_dir / "course_introductions.json").read_text(encoding="utf-8"))
    assert introductions == {
        "C001": {"default": {"zh": "", "en": "new"}},
        "PRESERVED": {"default": {"zh": "保留", "en": "preserved"}},
    }
    assert result.introductions.courses == 1
    assert result.introductions.updated == 1


def test_publish_rejects_invalid_existing_course_introductions(tmp_path: Path) -> None:
    data_dir = tmp_path / "major-data"
    data_dir.mkdir()
    (data_dir / "course_introductions.json").write_text("not json", encoding="utf-8")

    with pytest.raises(PublicationError, match="course_introductions"):
        publish_plans(
            data_dir,
            (plan(),),
            {"2025"},
            {"C001": {"default": {"zh": "简介", "en": "intro"}}},
        )
