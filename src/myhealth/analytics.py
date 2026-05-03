from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from .models import MetricSummary
from .storage import latest_datetime


SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
RESTING_HR_TYPE = "HKQuantityTypeIdentifierRestingHeartRate"
HRV_TYPE = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
STRENGTH_WORKOUTS = {
    "HKWorkoutActivityTypeTraditionalStrengthTraining",
    "HKWorkoutActivityTypeFunctionalStrengthTraining",
}


def parse_period(period: str) -> int:
    if not period.endswith("d"):
        raise ValueError("Period must look like 7d, 30d, or 90d.")
    days = int(period[:-1])
    if days <= 0:
        raise ValueError("Period days must be positive.")
    return days


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _quantity_values(conn: sqlite3.Connection, metric_type: str, start: datetime, end: datetime) -> list[float]:
    rows = conn.execute(
        """
        SELECT value FROM records
        WHERE type = ? AND value IS NOT NULL AND start_date >= ? AND start_date < ?
        """,
        (metric_type, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [float(row["value"]) for row in rows]


def _sleep_hours(conn: sqlite3.Connection, start: datetime, end: datetime) -> list[float]:
    rows = conn.execute(
        """
        SELECT start_date, end_date, value_text FROM records
        WHERE type = ? AND start_date >= ? AND start_date < ?
        """,
        (SLEEP_TYPE, start.isoformat(), end.isoformat()),
    ).fetchall()
    hours = []
    for row in rows:
        value_text = row["value_text"] or ""
        if "Asleep" not in value_text:
            continue
        started = datetime.fromisoformat(row["start_date"])
        ended = datetime.fromisoformat(row["end_date"])
        hours.append(max((ended - started).total_seconds() / 3600, 0))
    return hours


def summarize(conn: sqlite3.Connection, period_days: int) -> MetricSummary:
    end = latest_datetime(conn) or datetime.now().astimezone()
    start = end - timedelta(days=period_days)
    previous_start = start - timedelta(days=period_days)

    sleep_avg = _avg(_sleep_hours(conn, start, end))
    resting_avg = _avg(_quantity_values(conn, RESTING_HR_TYPE, start, end))
    hrv_avg = _avg(_quantity_values(conn, HRV_TYPE, start, end))
    previous_sleep = _avg(_sleep_hours(conn, previous_start, start))
    previous_resting = _avg(_quantity_values(conn, RESTING_HR_TYPE, previous_start, start))
    previous_hrv = _avg(_quantity_values(conn, HRV_TYPE, previous_start, start))

    workout_rows = conn.execute(
        """
        SELECT type, start_date FROM workouts
        WHERE start_date >= ? AND start_date < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    workout_sessions = len(workout_rows)
    strength_sessions = sum(1 for row in workout_rows if row["type"] in STRENGTH_WORKOUTS)
    active_days = len({row["start_date"][:10] for row in workout_rows})

    score = 70
    notes: list[str] = []
    if sleep_avg is not None:
        if sleep_avg >= 7:
            score += 10
            notes.append("Sleep is supporting training.")
        elif sleep_avg < 6:
            score -= 15
            notes.append("Sleep is below the usual recovery target.")
    if previous_sleep and sleep_avg and sleep_avg < previous_sleep - 0.75:
        score -= 10
        notes.append("Sleep is trending down.")
    if previous_hrv and hrv_avg:
        if hrv_avg < previous_hrv * 0.9:
            score -= 15
            notes.append("HRV is meaningfully below recent baseline.")
        elif hrv_avg > previous_hrv * 1.05:
            score += 5
            notes.append("HRV is above recent baseline.")
    if previous_resting and resting_avg:
        if resting_avg > previous_resting + 5:
            score -= 15
            notes.append("Resting heart rate is elevated versus baseline.")
        elif resting_avg < previous_resting - 3:
            score += 5
            notes.append("Resting heart rate is below recent baseline.")
    if strength_sessions >= max(3, period_days // 7 * 3):
        score -= 5
        notes.append("Strength frequency is high enough to watch recovery.")
    elif strength_sessions == 0:
        notes.append("No strength sessions were found in this period.")

    score = max(0, min(100, score))
    if score >= 75:
        status = "ready"
    elif score >= 55:
        status = "moderate"
    else:
        status = "recovery"

    return MetricSummary(
        period_days=period_days,
        sleep_hours_avg=sleep_avg,
        resting_hr_avg=resting_avg,
        hrv_ms_avg=hrv_avg,
        strength_sessions=strength_sessions,
        workout_sessions=workout_sessions,
        active_days=active_days,
        recovery_score=score,
        recovery_status=status,
        notes=notes or ["Not enough trend data yet; recommendations use available signals."],
    )

