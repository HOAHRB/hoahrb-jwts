from dataclasses import replace

import pytest

from hoa_cli.discovery import discover_plans
from hoa_cli.errors import ValidationError
from hoa_cli.major_rules import rule_for
from hoa_cli.models import Catalog, Department, Major, SourceCourse


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
def test_discovery_skips_explicitly_ignored_major_code(code: str) -> None:
    class IgnoredMajorGateway(FakeGateway):
        def get_majors(self, year: str, department: Department) -> tuple[Major, ...]:
            if year == "2024" and department.code == "35":
                return (Major(year, department.code, department.name, code, "忽略专业"),)
            return ()

        def get_plan(self, major: Major) -> tuple[SourceCourse, ...]:
            raise AssertionError("ignored major must not be queried")

    with pytest.raises(ValidationError, match="no valid execution plans"):
        discover_plans(IgnoredMajorGateway(), ("2024",))


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


def test_discovery_skips_only_w_variants_that_are_course_subsets() -> None:
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

    assert [plan.major.code for plan in plans] == ["35158", "35159W"]


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
