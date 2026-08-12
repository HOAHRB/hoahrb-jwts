from dataclasses import replace

import pytest
import toml

from hoahrb_jwts.errors import ValidationError
from hoahrb_jwts.models import DiscoveredPlan, Major, SourceCourse
from hoahrb_jwts.normalize import normalize_course, normalize_plan, plan_to_dict


def source_course(**overrides: str | None) -> SourceCourse:
    course = SourceCourse(
        code="22AD11001",
        name="思想政治理论实践课",
        english_name="Practicum",
        academic_year="1",
        term="秋季",
        offering_department="学生工作部（处）/团委",
        nature="必修",
        category="思政",
        track=None,
        credit="2.0",
        total_hours="32",
        examination_indicator="是",
    )
    return replace(course, **overrides)


def discovered_plan(year: str = "2025", major_code: str = "35158") -> DiscoveredPlan:
    return DiscoveredPlan(
        major=Major(year, "35", "人文社科学部", major_code, "俄语"),
        courses=(source_course(),),
    )


def test_normalize_course_maps_verified_fields() -> None:
    normalized = normalize_course(source_course(total_hours="32", examination_indicator="是"))
    assert normalized.course_code == "22AD11001"
    assert normalized.credit == 2.0
    assert normalized.assessment_method == "考试"
    assert normalized.recommended_year_semester == "第一学年秋季"
    assert normalized.total_hours == 32


@pytest.mark.parametrize("academic_year", ["1", "2", "3", "4", "5"])
@pytest.mark.parametrize("term", ["秋季", "春季", "夏季", "寒假", "暑假"])
def test_normalize_course_supports_observed_years_and_terms(academic_year: str, term: str) -> None:
    normalized = normalize_course(source_course(academic_year=academic_year, term=term))
    assert normalized.recommended_year_semester.endswith(term)


def test_non_exam_indicator_maps_to_assessment() -> None:
    assert normalize_course(source_course(examination_indicator="否")).assessment_method == "考查"


def test_blank_exam_indicator_maps_to_assessment() -> None:
    assert normalize_course(source_course(examination_indicator="")).assessment_method == "考查"


def test_unknown_exam_indicator_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown examination indicator"):
        normalize_course(source_course(examination_indicator="未知"))


def test_week_based_hours_are_not_invented() -> None:
    normalized = normalize_course(source_course(total_hours="3周", examination_indicator=None))
    assert normalized.total_hours is None
    assert normalized.assessment_method is None


def test_plan_id_is_deterministic() -> None:
    plan = normalize_plan(discovered_plan())
    assert plan.plan_id == "HIT-2025-35158"
    assert plan.category == "本"


def test_plan_to_dict_uses_compatibility_keys_and_omits_unknown_fields() -> None:
    data = plan_to_dict(normalize_plan(discovered_plan()))
    assert data["info"]["plan_ID"] == "HIT-2025-35158"
    assert data["info"]["plan_category"] == "本"
    course = data["courses"][0]
    assert "hours" not in course
    assert "track" not in course
    round_tripped = toml.loads(toml.dumps(data))
    assert round_tripped["info"] == data["info"]
    assert round_tripped["courses"][0] == course


@pytest.mark.parametrize(
    ("field", "value"),
    [("academic_year", "6"), ("term", "未知"), ("credit", "not-a-number"), ("credit", "-1")],
)
def test_normalize_course_rejects_unverified_required_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        normalize_course(source_course(**{field: value}))
