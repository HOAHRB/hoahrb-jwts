from hoa_cli.models import Department, Major, SourceCourse


def test_source_domain_records_are_frozen_and_typed() -> None:
    department = Department(code="35", name="人文社科学部")
    major = Major(
        year="2025",
        department_code=department.code,
        department_name=department.name,
        code="35158",
        name="俄语",
    )
    course = SourceCourse(
        code="22FL22230",
        name="俄语实践语法（1）",
        english_name="Russian Practical Grammar I",
        academic_year="1",
        term="秋季",
        offering_department="外国语学院",
        nature="必修",
        category="专业课",
        track=None,
        credit="2.0",
        total_hours="32",
        examination_indicator="是",
    )
    assert major.department_name == department.name
    assert course.credit == "2.0"
