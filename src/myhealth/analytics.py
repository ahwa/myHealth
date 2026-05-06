from __future__ import annotations

import sqlite3
import statistics
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


def _clean_health_type(value: str | None) -> str:
    if not value:
        return "Unknown"
    return (
        value.removeprefix("HKQuantityTypeIdentifier")
        .removeprefix("HKCategoryTypeIdentifier")
        .removeprefix("HKWorkoutActivityType")
    )


def parse_period(period: str) -> int:
    """Parse '7d', '30d', '4w' and the like into a positive number of days."""
    if not period:
        raise ValueError("Period cannot be empty.")
    unit = period[-1].lower()
    multiplier = {"d": 1, "w": 7}.get(unit)
    if multiplier is None:
        raise ValueError(f"Period must end with 'd' or 'w' (got {period!r}).")
    try:
        amount = int(period[:-1])
    except ValueError as exc:
        raise ValueError(f"Period must look like 7d or 4w (got {period!r}).") from exc
    days = amount * multiplier
    if days <= 0:
        raise ValueError("Period must be positive.")
    return days


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: list[float]) -> float | None:
    """Sample standard deviation; needs at least 2 points."""
    return statistics.stdev(values) if len(values) >= 2 else None


def daily_series(conn: sqlite3.Connection, period_days: int) -> dict:
    """Return aligned per-day arrays for the analysis window.

    Output keys: dates (list[str] yyyy-mm-dd), sleep_hours, resting_hr, hrv_ms
    (each list[float | None] of length period_days), and workouts (list[int]).
    """
    end = latest_datetime(conn) or datetime.now().astimezone()
    start = end - timedelta(days=period_days)
    # Enumerate the period_days days ending on end.date() (inclusive) so the
    # latest data point always has a slot.
    end_day = end.date()
    days = [end_day - timedelta(days=period_days - 1 - i) for i in range(period_days)]
    date_strs = [d.isoformat() for d in days]
    idx = {s: i for i, s in enumerate(date_strs)}

    def _avg_by_day(metric_type: str) -> list[float | None]:
        out: list[list[float]] = [[] for _ in days]
        rows = conn.execute(
            """
            SELECT substr(start_date, 1, 10) AS d, value FROM records
            WHERE type = ? AND value IS NOT NULL AND start_date >= ? AND start_date < ?
            """,
            (metric_type, start.isoformat(), end.isoformat()),
        ).fetchall()
        for row in rows:
            i = idx.get(row["d"])
            if i is not None and row["value"] is not None:
                out[i].append(float(row["value"]))
        return [round(sum(v) / len(v), 2) if v else None for v in out]

    # Sleep: sum 'Asleep*' segments per night, bucketed by the WAKE date (the
    # segment's end date). Matches Apple Health's UI and keeps a night that
    # crosses midnight on a single column.
    sleep_per_day: list[float | None] = [None] * len(days)
    sleep_rows = conn.execute(
        """
        SELECT start_date, end_date, value_text
        FROM records
        WHERE type = ? AND start_date >= ? AND start_date < ?
        """,
        (SLEEP_TYPE, start.isoformat(), end.isoformat()),
    ).fetchall()
    sleep_buckets: dict[str, float] = {}
    for row in sleep_rows:
        if "Asleep" not in (row["value_text"] or ""):
            continue
        s = datetime.fromisoformat(row["start_date"])
        e = datetime.fromisoformat(row["end_date"])
        wake_day = e.date().isoformat()
        sleep_buckets[wake_day] = sleep_buckets.get(wake_day, 0.0) + max((e - s).total_seconds() / 3600, 0)
    for d, hrs in sleep_buckets.items():
        i = idx.get(d)
        if i is not None:
            sleep_per_day[i] = round(hrs, 2)

    workouts_per_day = [0] * len(days)
    rows = conn.execute(
        "SELECT substr(start_date, 1, 10) AS d FROM workouts WHERE start_date >= ? AND start_date < ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    for row in rows:
        i = idx.get(row["d"])
        if i is not None:
            workouts_per_day[i] += 1

    return {
        "dates": date_strs,
        "sleep_hours": sleep_per_day,
        "resting_hr": _avg_by_day(RESTING_HR_TYPE),
        "hrv_ms": _avg_by_day(HRV_TYPE),
        "workouts": workouts_per_day,
    }


def inspect_database(conn: sqlite3.Connection, limit: int = 20) -> dict:
    """Return record/workout/source inventory for the imported Health cache."""
    record_total = int(conn.execute("SELECT COUNT(*) AS c FROM records").fetchone()["c"])
    workout_total = int(conn.execute("SELECT COUNT(*) AS c FROM workouts").fetchone()["c"])
    first = conn.execute(
        """
        SELECT MIN(min_date) AS first FROM (
          SELECT MIN(start_date) AS min_date FROM records
          UNION ALL
          SELECT MIN(start_date) AS min_date FROM workouts
        )
        """
    ).fetchone()["first"]
    latest = conn.execute(
        """
        SELECT MAX(max_date) AS latest FROM (
          SELECT MAX(end_date) AS max_date FROM records
          UNION ALL
          SELECT MAX(end_date) AS max_date FROM workouts
        )
        """
    ).fetchone()["latest"]

    record_types = [
        {"type": _clean_health_type(row["type"]), "raw_type": row["type"], "count": int(row["c"])}
        for row in conn.execute(
            """
            SELECT type, COUNT(*) AS c FROM records
            GROUP BY type ORDER BY c DESC, type ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]
    workout_types = [
        {"type": _clean_health_type(row["type"]), "raw_type": row["type"], "count": int(row["c"])}
        for row in conn.execute(
            """
            SELECT type, COUNT(*) AS c FROM workouts
            GROUP BY type ORDER BY c DESC, type ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]
    sources = [
        {"source": row["source"] or "Unknown", "kind": row["kind"], "count": int(row["c"])}
        for row in conn.execute(
            """
            SELECT source, kind, COUNT(*) AS c FROM (
              SELECT source, 'record' AS kind FROM records
              UNION ALL
              SELECT source, 'workout' AS kind FROM workouts
            )
            GROUP BY source, kind ORDER BY c DESC, source ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]

    return {
        "records": record_total,
        "workouts": workout_total,
        "first_date": first,
        "latest_date": latest,
        "record_types": record_types,
        "workout_types": workout_types,
        "sources": sources,
    }


def _quantity_distinct_days(
    conn: sqlite3.Connection, metric_type: str, start: datetime, end: datetime
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT substr(start_date, 1, 10)) AS d FROM records
        WHERE type = ? AND value IS NOT NULL AND start_date >= ? AND start_date < ?
        """,
        (metric_type, start.isoformat(), end.isoformat()),
    ).fetchone()
    return int(row["d"]) if row and row["d"] is not None else 0


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
    """Total sleep hours per night in [start, end).

    Apple Health writes each sleep stage (AsleepCore/Deep/REM/Unspecified) as a
    separate record, often dozens of segments per night. We bucket segments by
    the date they ENDED (the wake date) — that matches Apple Health's UI and
    avoids splitting one night across two days when sleep crosses midnight.
    Returns one float per distinct night, summed across that night's segments.
    """
    rows = conn.execute(
        """
        SELECT end_date, start_date, value_text FROM records
        WHERE type = ? AND start_date >= ? AND start_date < ?
        """,
        (SLEEP_TYPE, start.isoformat(), end.isoformat()),
    ).fetchall()
    by_night: dict[str, float] = {}
    for row in rows:
        value_text = row["value_text"] or ""
        if "Asleep" not in value_text:
            continue
        started = datetime.fromisoformat(row["start_date"])
        ended = datetime.fromisoformat(row["end_date"])
        seconds = max((ended - started).total_seconds(), 0)
        night = ended.date().isoformat()
        by_night[night] = by_night.get(night, 0.0) + seconds / 3600
    return [round(v, 3) for v in by_night.values()]


def summarize(conn: sqlite3.Connection, period_days: int) -> MetricSummary:
    end = latest_datetime(conn) or datetime.now().astimezone()
    start = end - timedelta(days=period_days)
    previous_start = start - timedelta(days=period_days)

    sleep_values = _sleep_hours(conn, start, end)
    rhr_values = _quantity_values(conn, RESTING_HR_TYPE, start, end)
    hrv_values = _quantity_values(conn, HRV_TYPE, start, end)
    sleep_avg = _avg(sleep_values)
    sleep_stdev = _stdev(sleep_values)
    resting_avg = _avg(rhr_values)
    resting_stdev = _stdev(rhr_values)
    hrv_avg = _avg(hrv_values)
    hrv_stdev = _stdev(hrv_values)
    previous_sleep = _avg(_sleep_hours(conn, previous_start, start))
    previous_resting = _avg(_quantity_values(conn, RESTING_HR_TYPE, previous_start, start))
    previous_hrv = _avg(_quantity_values(conn, HRV_TYPE, previous_start, start))

    workout_rows = conn.execute(
        """
        SELECT type, start_date, duration_minutes FROM workouts
        WHERE start_date >= ? AND start_date < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    workout_sessions = len(workout_rows)
    strength_sessions = sum(1 for row in workout_rows if row["type"] in STRENGTH_WORKOUTS)
    active_days = len({row["start_date"][:10] for row in workout_rows})
    workouts_by_type: dict[str, int] = {}
    total_minutes = 0.0
    for row in workout_rows:
        # Strip Apple's HKWorkoutActivityType prefix for human-readable keys.
        clean = _clean_health_type(row["type"])
        workouts_by_type[clean] = workouts_by_type.get(clean, 0) + 1
        if row["duration_minutes"] is not None:
            total_minutes += float(row["duration_minutes"])
    total_active_hours = round(total_minutes / 60, 3) if workout_rows else None

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

    weekly_strength = (strength_sessions * 7 / period_days) if period_days > 0 else None
    completeness = {
        "sleep_nights": len(sleep_values),
        "resting_hr_days": _quantity_distinct_days(conn, RESTING_HR_TYPE, start, end),
        "hrv_days": _quantity_distinct_days(conn, HRV_TYPE, start, end),
        "workout_days": active_days,
    }

    trend_changes: dict[str, float] = {}
    if sleep_avg is not None and previous_sleep is not None:
        trend_changes["sleep_hours_delta"] = round(sleep_avg - previous_sleep, 3)
    if resting_avg is not None and previous_resting is not None:
        trend_changes["resting_hr_delta"] = round(resting_avg - previous_resting, 3)
    if hrv_avg is not None and previous_hrv is not None:
        trend_changes["hrv_ms_delta"] = round(hrv_avg - previous_hrv, 3)

    no_data = (
        completeness["sleep_nights"] == 0
        and completeness["resting_hr_days"] == 0
        and completeness["hrv_days"] == 0
        and workout_sessions == 0
    )
    if no_data:
        notes.append("No data available for this period; import an Apple Health export first.")

    return MetricSummary(
        period_days=period_days,
        sleep_hours_avg=sleep_avg,
        sleep_hours_stdev=sleep_stdev,
        resting_hr_avg=resting_avg,
        resting_hr_stdev=resting_stdev,
        hrv_ms_avg=hrv_avg,
        hrv_ms_stdev=hrv_stdev,
        strength_sessions=strength_sessions,
        workout_sessions=workout_sessions,
        active_days=active_days,
        weekly_strength_frequency=weekly_strength,
        total_active_hours=total_active_hours,
        workouts_by_type=workouts_by_type,
        data_completeness=completeness,
        trend_changes=trend_changes,
        recovery_score=score,
        recovery_status=status,
        notes=notes or ["Not enough trend data yet; recommendations use available signals."],
    )
