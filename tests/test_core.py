from pathlib import Path

from myhealth.analytics import parse_period, summarize
from myhealth.apple_health import parse_export
from myhealth.models import PlanningStyle
from myhealth.planner import make_plan
from myhealth.storage import connect, insert_items


FIXTURE = Path(__file__).parent / "fixtures" / "export.xml"


def test_parse_export_reads_records_and_workouts():
    items = list(parse_export(FIXTURE))

    assert len(items) == 7
    assert sum(1 for item in items if item.__class__.__name__ == "Workout") == 2


def test_summarize_fixture_data(tmp_path):
    conn = connect(tmp_path / "health.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    summary = summarize(conn, parse_period("30d"))

    assert summary.strength_sessions == 2
    assert summary.workout_sessions == 2
    assert summary.sleep_hours_avg == 7.5
    assert summary.recovery_score >= 70


def test_planning_styles_change_recovery_thresholds():
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)
    low_summary = summary.copy(update={"recovery_score": 50})

    conservative = make_plan(low_summary, 3, PlanningStyle.conservative)
    aggressive = make_plan(low_summary, 3, PlanningStyle.aggressive)

    assert conservative[0].recommendation == "Mobility and recovery"
    assert aggressive[0].recommendation != "Mobility and recovery"

