from dataclasses import replace

import pytest

from hoahrb_jwts.discovery import discover_plans
from hoahrb_jwts.errors import ValidationError
from hoahrb_jwts.major_rules import rule_for
from hoahrb_jwts.models import Catalog, Department, Major, SourceCourse


def source_course(code: str = "C001", **overrides: str | None) -> SourceCourse:
    course = SourceCourse(
        code=code,
        name="课程",
        english_name=None,
        academic_year="1",
        term="秋季",
        offering_department="学院",
        nature="必修",
        category="专业课",
        track=None,
        credit="2.0",
        total_hours="32",
        examination_indicator="否",
    )
    return replace(course, **overrides)


class FakeGateway:
    def __init__(self, conflict: bool = False) -> None:
        self.catalog = Catalog(
            years=("2025", "2024"),
            departments=(Department("35", "人文社科学部"), Department("08", "历史院系")),
        )
        self.conflict = conflict
        self.major_calls: list[tuple[str, str]] = []

    def get_catalog(self) -> Catalog:
        return self.catalog

    def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
        self.major_calls.append((year, department.code))
        if year == "2025" and department.code == "35":
            name = "俄语" if not self.conflict else "俄语（冲突）"
            return (Major(year, department.code, department.name, "35158", name),)
        return ()

    def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
        return (source_course(),)


def test_discovery_does_not_infer_major_across_years() -> None:
    gateway = FakeGateway()
    plans = discover_plans(gateway, ("2025",))
    assert [(p.major.year, p.major.code) for p in plans] == [("2025", "35158")]
    assert ("2025", "35") in gateway.major_calls
    assert ("2025", "08") in gateway.major_calls


def test_discovery_skips_empty_plans_but_requires_each_year() -> None:
    class EmptyGateway(FakeGateway):
        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            if year == "2024" and department.code == "35":
                return (Major(year, department.code, department.name, "old", "旧专业"),)
            return super().get_majors(year, department)

        def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
            if major.year == "2024":
                return ()
            return super().get_plan(major)

    with pytest.raises(ValidationError, match="no valid execution plans for requested year 2024"):
        discover_plans(EmptyGateway(), ("2024", "2025"))


def test_identical_duplicate_course_rows_collapse() -> None:
    class DuplicateGateway(FakeGateway):
        def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
            course = source_course()
            return (course, course)

    plans = discover_plans(DuplicateGateway(), ("2025",))
    assert len(plans[0].courses) == 1


@pytest.mark.parametrize("code", ["01182L", "35E233"])
def test_discovery_preserves_previously_ignored_distinct_plans(code: str) -> None:
    class IgnoredMajorGateway(FakeGateway):
        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            if year == "2024" and department.code == "35":
                return (Major(year, department.code, department.name, code, "忽略专业"),)
            return ()

        def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
            return (source_course(),)

    plans = discover_plans(IgnoredMajorGateway(), ("2024",))
    assert [plan.major.code for plan in plans] == [code]


def test_future_field_names_preserve_filename_and_mapping(tmp_path) -> None:
    from hoahrb_jwts.normalize import normalize_plan
    from hoahrb_jwts.writer import publish_plans

    class FutureGateway(FakeGateway):
        def get_catalog(self) -> Catalog:
            return Catalog(("2023",), (Department("52", "未来技术学院"),))

        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            return tuple(
                Major(year, "52", department.name, code, "精密仪器")
                for code in ("52WL014", "52WL014Z")
            )

    plans = tuple(normalize_plan(plan) for plan in discover_plans(FutureGateway(), ("2023",)))
    summary = publish_plans(tmp_path, plans, {"2023"})
    assert summary.filename_fallbacks == (
        "本_2023_未来技术学院_精密仪器_52WL014.toml",
        "本_2023_未来技术学院_精密仪器_52WL014Z.toml",
    )
    import json

    import toml

    mapping = json.loads((tmp_path / "major_mapping.json").read_text(encoding="utf-8"))
    for code, field in (("52WL014", "人工智能领域"), ("52WL014Z", "智能制造领域")):
        name = f"精密仪器（{field}）"
        assert mapping["2023"][code]["name"] == name
        assert (
            toml.load(tmp_path / "plans" / f"本_2023_未来技术学院_精密仪器_{code}.toml")["info"][
                "major_name"
            ]
            == name
        )
    assert rule_for("2025", "52WL014") is None
    assert rule_for("2024", "52WL036Z") is None
    assert rule_for("2022", "52WL190R").name == "材料科学与工程（智能制造领域）"
    assert rule_for("2023", "52WL190RS").name == "材料科学与工程（人工智能领域）"


def test_discovery_applies_curated_name_to_missing_class_designation() -> None:
    class CuratedNameGateway(FakeGateway):
        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            if year == "2024" and department.code == "35":
                return (
                    Major(year, department.code, department.name, "09331", "土木工程"),
                    Major(year, department.code, department.name, "35158", "俄语"),
                )
            return ()

    plans = discover_plans(CuratedNameGateway(), ("2024",))

    assert {plan.major.code: plan.major.name for plan in plans} == {
        "09331": "土木工程（土木菁华班）",
        "35158": "俄语（火箭系统与航天）",
    }


def test_major_rules_are_scoped_to_explicit_years() -> None:
    assert rule_for("2024", "09331").name == "土木工程（土木菁华班）"
    assert rule_for("2025", "09331") is None
    assert rule_for("2025", "35158").name == "俄语（火箭系统与航天）"
    assert rule_for("2023", "25145").category == "本科国际生"
    assert rule_for("2024", "25145") is None


def test_category_rule_preserves_same_name_domestic_and_international_plans(tmp_path) -> None:
    from hoahrb_jwts.normalize import normalize_plan
    from hoahrb_jwts.writer import publish_plans

    class ChemistryGateway(FakeGateway):
        def get_catalog(self) -> Catalog:
            return Catalog(("2023", "2024"), (Department("25", "化工与化学学院"),))

        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            return tuple(
                Major(year, "25", department.name, code, "化学工程与工艺", "本")
                for code in ("25142", "25145")
            )

    plans = discover_plans(ChemistryGateway(), ("2023",))
    assert {plan.major.code: plan.major.category for plan in plans} == {
        "25142": "本",
        "25145": "本科国际生",
    }
    result = publish_plans(tmp_path, tuple(normalize_plan(plan) for plan in plans), {"2023"})
    assert result.added == 2
    assert (tmp_path / "plans" / "本_2023_化工与化学学院_化学工程与工艺.toml").exists()
    assert (tmp_path / "plans" / "本科国际生_2023_化工与化学学院_化学工程与工艺.toml").exists()
    assert all(
        plan.major.category == "本" for plan in discover_plans(ChemistryGateway(), ("2024",))
    )


def test_discovery_preserves_w_variants_even_when_courses_are_subsets() -> None:
    class WVariantGateway(FakeGateway):
        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            if year == "2025" and department.code == "35":
                return (
                    Major(year, department.code, department.name, "35158", "俄语"),
                    Major(year, department.code, department.name, "35158W", "俄语"),
                    Major(year, department.code, department.name, "35159W", "保留专业"),
                )
            return ()

        def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
            if major.code == "35158":
                return (source_course("C001"), source_course("C002"))
            if major.code == "35158W":
                return (source_course("C001"),)
            return (source_course("C003"),)

    plans = discover_plans(WVariantGateway(), ("2025",))

    assert {plan.major.code: plan.major.name for plan in plans} == {
        "35158": "俄语（火箭系统与航天）",
        "35158W": "俄语（威海拔尖班）",
        "35159W": "保留专业（威海拔尖班）",
    }


@pytest.mark.parametrize("name", ["材料成型及控制工程", "材料成型及控制工程（威海拔尖班）"])
def test_w_variant_names_allow_both_plans_to_be_published(name: str, tmp_path) -> None:
    from hoahrb_jwts.normalize import normalize_plan
    from hoahrb_jwts.writer import publish_plans

    class WGateway(FakeGateway):
        def get_catalog(self) -> Catalog:
            return Catalog(("2023",), (Department("03", "材料科学与工程学院"),))

        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            return (
                Major(year, "03", department.name, "03091", "材料成型及控制工程"),
                Major(year, "03", department.name, "03091W", name),
            )

    plans = discover_plans(WGateway(), ("2023",))
    result = publish_plans(tmp_path, tuple(normalize_plan(plan) for plan in plans), {"2023"})

    assert result.added == 2
    assert (tmp_path / "plans" / "本_2023_材料科学与工程学院_材料成型及控制工程.toml").exists()
    assert (
        tmp_path / "plans" / "本_2023_材料科学与工程学院_材料成型及控制工程（威海拔尖班）.toml"
    ).exists()


def test_same_course_code_with_different_tracks_is_preserved() -> None:
    class TrackGateway(FakeGateway):
        def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
            return (source_course(track="方向甲"), source_course(track="方向乙"))

    plans = discover_plans(TrackGateway(), ("2025",))
    assert [course.track for course in plans[0].courses] == ["方向甲", "方向乙"]


def test_same_course_code_with_different_schedule_is_preserved() -> None:
    class ConflictGateway(FakeGateway):
        def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
            return (source_course(), source_course(academic_year="2"))

    plans = discover_plans(ConflictGateway(), ("2025",))
    assert [course.academic_year for course in plans[0].courses] == ["1", "2"]


def test_conflicting_duplicate_plan_identity_raises() -> None:
    class DuplicateMajorGateway(FakeGateway):
        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            if year == "2025":
                return (
                    Major(year, "35", "人文社科学部", "35158", "俄语"),
                    Major(year, "08", "历史院系", "35158", "俄语"),
                )
            return ()

    with pytest.raises(ValidationError, match="conflicting execution plans"):
        discover_plans(DuplicateMajorGateway(), ("2025",))


def test_conflicting_duplicate_metadata_raises_even_when_first_plan_is_empty() -> None:
    class EmptyThenConflictingGateway(FakeGateway):
        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            if year == "2025" and department.code == "35":
                return (Major(year, "35", "人文社科学部", "35158", "俄语"),)
            if year == "2025" and department.code == "08":
                return (Major(year, "08", "历史院系", "35158", "俄语"),)
            return ()

        def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
            if major.department_code == "35":
                return ()
            return (source_course(),)

    with pytest.raises(ValidationError, match="conflicting execution plans"):
        discover_plans(EmptyThenConflictingGateway(), ("2025",))
