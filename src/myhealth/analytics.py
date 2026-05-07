from __future__ import annotations

import sqlite3
import statistics
from datetime import date, datetime, timedelta

from .models import HabitStreak, HabitStreaks, MetricSummary, PersonalRecord, PersonalRecords
from .storage import latest_datetime


SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
RESTING_HR_TYPE = "HKQuantityTypeIdentifierRestingHeartRate"
HRV_TYPE = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
STEP_COUNT_TYPE = "HKQuantityTypeIdentifierStepCount"
ACTIVE_ENERGY_TYPE = "HKQuantityTypeIdentifierActiveEnergyBurned"
DISTANCE_WALK_RUN_TYPE = "HKQuantityTypeIdentifierDistanceWalkingRunning"
EXERCISE_TIME_TYPE = "HKQuantityTypeIdentifierAppleExerciseTime"
PHYSICAL_EFFORT_TYPE = "HKQuantityTypeIdentifierPhysicalEffort"
STRENGTH_WORKOUTS = {
    "HKWorkoutActivityTypeTraditionalStrengthTraining",
    "HKWorkoutActivityTypeFunctionalStrengthTraining",
}
RUNNING_WORKOUT = "HKWorkoutActivityTypeRunning"
WALKING_WORKOUT = "HKWorkoutActivityTypeWalking"


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

    Output keys: dates (list[str] yyyy-mm-dd), sleep_hours, resting_hr, hrv_ms,
    steps, active_energy_kcal, exercise_minutes, active_hours, distance_mi,
    physical_effort, and workouts.
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

    def _sum_by_day(metric_type: str) -> list[float | None]:
        out = [0.0 for _ in days]
        seen = [False for _ in days]
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
                out[i] += float(row["value"])
                seen[i] = True
        return [round(v, 2) if seen[i] else None for i, v in enumerate(out)]

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
    workout_minutes_per_day = [0.0] * len(days)
    rows = conn.execute(
        """
        SELECT substr(start_date, 1, 10) AS d, duration_minutes
        FROM workouts WHERE start_date >= ? AND start_date < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    for row in rows:
        i = idx.get(row["d"])
        if i is not None:
            workouts_per_day[i] += 1
            if row["duration_minutes"] is not None:
                workout_minutes_per_day[i] += float(row["duration_minutes"])

    return {
        "dates": date_strs,
        "sleep_hours": sleep_per_day,
        "resting_hr": _avg_by_day(RESTING_HR_TYPE),
        "hrv_ms": _avg_by_day(HRV_TYPE),
        "steps": _sum_by_day(STEP_COUNT_TYPE),
        "active_energy_kcal": _sum_by_day(ACTIVE_ENERGY_TYPE),
        "exercise_minutes": _sum_by_day(EXERCISE_TIME_TYPE),
        "active_hours": [round(v / 60, 2) if v else 0 for v in workout_minutes_per_day],
        "distance_mi": _sum_by_day(DISTANCE_WALK_RUN_TYPE),
        "physical_effort": _avg_by_day(PHYSICAL_EFFORT_TYPE),
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


def _workout_pr(
    conn: sqlite3.Connection,
    types: set[str] | None,
    column: str,
    label: str,
    unit: str,
    higher_is_better: bool = True,
    transform=None,
    round_dp: int | None = None,
) -> PersonalRecord | None:
    """Return the best workout row's PR for ``column`` across ``types``.

    ``types`` None means any workout type. ``transform`` is an optional callable
    applied to the raw value before rounding. Ties resolve to the earliest date.
    """
    agg = "MAX" if higher_is_better else "MIN"
    params: list = []
    where = [f"{column} IS NOT NULL"]
    if types:
        placeholders = ",".join("?" for _ in types)
        where.append(f"type IN ({placeholders})")
        params.extend(sorted(types))
    where_sql = " AND ".join(where)
    # Pick the winning value first, then the earliest row tied to that value.
    best_row = conn.execute(
        f"""
        SELECT {column} AS v, substr(start_date, 1, 10) AS d
        FROM workouts WHERE {where_sql}
        ORDER BY {column} {'DESC' if higher_is_better else 'ASC'}, start_date ASC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not best_row or best_row["v"] is None:
        return None
    value = float(best_row["v"])
    if transform is not None:
        value = transform(value)
    if round_dp is not None:
        value = round(value, round_dp)
    return PersonalRecord(
        metric=label,
        value=value,
        unit=unit,
        occurred_on=date.fromisoformat(best_row["d"]),
        higher_is_better=higher_is_better,
    )


def _grouped_daily_pr(
    conn: sqlite3.Connection,
    metric_type: str,
    label: str,
    unit: str,
    higher_is_better: bool = True,
    aggregate: str = "SUM",
    round_dp: int | None = None,
) -> PersonalRecord | None:
    """Return the PR for a records metric grouped by day.

    ``aggregate`` chooses how rows within a day are combined (e.g. SUM for steps,
    MAX for a raw-row metric that should NOT be summed). ``higher_is_better``
    determines which day wins. Ties resolve to the earliest date.
    """
    best_agg = "MAX" if higher_is_better else "MIN"
    rows = conn.execute(
        f"""
        SELECT substr(start_date, 1, 10) AS d, {aggregate}(value) AS v
        FROM records
        WHERE type = ? AND value IS NOT NULL
        GROUP BY substr(start_date, 1, 10)
        ORDER BY v {'DESC' if higher_is_better else 'ASC'}, d ASC
        LIMIT 1
        """,
        (metric_type,),
    ).fetchone()
    if not rows or rows["v"] is None:
        return None
    value = float(rows["v"])
    if round_dp is not None:
        value = round(value, round_dp)
    return PersonalRecord(
        metric=label,
        value=value,
        unit=unit,
        occurred_on=date.fromisoformat(rows["d"]),
        higher_is_better=higher_is_better,
    )


def _sleep_pr(conn: sqlite3.Connection) -> PersonalRecord | None:
    """Longest sleep night (sum Asleep* segments bucketed by wake date)."""
    rows = conn.execute(
        """
        SELECT start_date, end_date, value_text FROM records
        WHERE type = ?
        """,
        (SLEEP_TYPE,),
    ).fetchall()
    by_night: dict[str, float] = {}
    for row in rows:
        if "Asleep" not in (row["value_text"] or ""):
            continue
        started = datetime.fromisoformat(row["start_date"])
        ended = datetime.fromisoformat(row["end_date"])
        hours = max((ended - started).total_seconds(), 0) / 3600
        night = ended.date().isoformat()
        by_night[night] = by_night.get(night, 0.0) + hours
    if not by_night:
        return None
    best_hours = max(by_night.values())
    # Earliest-date tiebreak.
    best_day = min(d for d, h in by_night.items() if h == best_hours)
    return PersonalRecord(
        metric="Longest sleep night",
        value=round(best_hours, 2),
        unit="h",
        occurred_on=date.fromisoformat(best_day),
        higher_is_better=True,
    )


def _workout_days(conn: sqlite3.Connection, since: date | None = None) -> set[date]:
    params: list = []
    where = ""
    if since is not None:
        where = "WHERE start_date >= ?"
        params.append(since.isoformat())
    rows = conn.execute(
        f"SELECT DISTINCT substr(start_date, 1, 10) AS d FROM workouts {where}",
        params,
    ).fetchall()
    return {date.fromisoformat(row["d"]) for row in rows if row["d"]}


def _exercise_days(conn: sqlite3.Connection, since: date, threshold: float = 30.0) -> set[date]:
    rows = conn.execute(
        """
        SELECT substr(start_date, 1, 10) AS d, SUM(value) AS total
        FROM records
        WHERE type = ? AND value IS NOT NULL AND start_date >= ?
        GROUP BY substr(start_date, 1, 10)
        HAVING total >= ?
        """,
        (EXERCISE_TIME_TYPE, since.isoformat(), threshold),
    ).fetchall()
    return {date.fromisoformat(row["d"]) for row in rows if row["d"]}


def _current_streak(days: set[date], anchor: date) -> int:
    if not days or anchor not in days:
        return 0
    streak = 0
    cursor = anchor
    while cursor in days:
        streak += 1
        cursor = cursor - timedelta(days=1)
    return streak


def _longest_streak(days: set[date]) -> int:
    if not days:
        return 0
    sorted_days = sorted(days)
    best = 1
    current = 1
    for prev, curr in zip(sorted_days, sorted_days[1:]):
        if curr - prev == timedelta(days=1):
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


DEFAULT_STREAK_THRESHOLDS: dict[str, tuple[float, str]] = {
    "sleep_hours":     (7.0,    ">="),
    "steps":           (8000.0, ">="),
    "hrv_ms":          (50.0,   ">="),
    "active_calories": (400.0,  ">="),
    "rhr":             (60.0,   "<="),
}

# Maps canonical streak metric key -> daily_series list key
_STREAK_SERIES_KEY: dict[str, str] = {
    "sleep_hours":     "sleep_hours",
    "steps":           "steps",
    "hrv_ms":          "hrv_ms",
    "active_calories": "active_energy_kcal",
    "rhr":             "resting_hr",
}


def _meets(value: float | None, threshold: float, direction: str) -> bool:
    if value is None:
        return False
    return value >= threshold if direction == ">=" else value <= threshold


def compute_streaks(
    conn: sqlite3.Connection,
    period_days: int = 90,
    thresholds: dict[str, float] | None = None,
) -> HabitStreaks:
    """Compute habit streaks for the default set of metrics over the given window."""
    latest = latest_datetime(conn)
    if latest is None:
        return HabitStreaks(period_days=period_days, anchor_date=None, streaks=[])

    anchor_date = latest.date()
    series = daily_series(conn, period_days)
    dates = series["dates"]  # oldest -> newest

    streaks: list[HabitStreak] = []
    for metric_key, (default_threshold, direction) in DEFAULT_STREAK_THRESHOLDS.items():
        threshold = (thresholds or {}).get(metric_key, default_threshold)
        series_key = _STREAK_SERIES_KEY[metric_key]
        values = series[series_key]
        met = [_meets(v, threshold, direction) for v in values]

        # Longest streak: single-pass max run
        longest = 0
        run = 0
        for m in met:
            if m:
                run += 1
                longest = max(longest, run)
            else:
                run = 0

        # Current streak: count trailing True from end
        current = 0
        for m in reversed(met):
            if m:
                current += 1
            else:
                break

        # last_met_date: highest index i where met[i] is True
        last_met_date = None
        for i in range(len(met) - 1, -1, -1):
            if met[i]:
                last_met_date = date.fromisoformat(dates[i])
                break

        streaks.append(HabitStreak(
            metric=metric_key,
            threshold=threshold,
            direction=direction,
            current_streak=current,
            longest_streak=longest,
            last_met_date=last_met_date,
            active_today=current >= 1,
        ))

    return HabitStreaks(
        period_days=period_days,
        anchor_date=anchor_date,
        streaks=streaks,
    )


def personal_records(conn: sqlite3.Connection, period_days: int = 90) -> PersonalRecords:
    """Compute all-time personal records and recent workout streaks."""
    records: list[PersonalRecord] = []

    # All-time records — each helper returns None if the underlying metric has
    # no rows, in which case we skip the PR (sparse list).
    longest_run = _workout_pr(
        conn,
        types={RUNNING_WORKOUT},
        column="distance_km",
        label="Longest run",
        unit="mi",
        transform=lambda v: v / 1.609344,
        round_dp=2,
    )
    if longest_run is not None:
        records.append(longest_run)

    longest_walk = _workout_pr(
        conn,
        types={WALKING_WORKOUT},
        column="distance_km",
        label="Longest walk",
        unit="mi",
        transform=lambda v: v / 1.609344,
        round_dp=2,
    )
    if longest_walk is not None:
        records.append(longest_walk)

    longest_workout = _workout_pr(
        conn,
        types=None,
        column="duration_minutes",
        label="Longest workout",
        unit="min",
        round_dp=1,
    )
    if longest_workout is not None:
        records.append(longest_workout)

    biggest_burn = _workout_pr(
        conn,
        types=None,
        column="total_energy_kcal",
        label="Biggest calorie burn (workout)",
        unit="kcal",
        round_dp=0,
    )
    if biggest_burn is not None:
        records.append(biggest_burn)

    most_steps = _grouped_daily_pr(
        conn,
        metric_type=STEP_COUNT_TYPE,
        label="Most steps in a day",
        unit="steps",
        aggregate="SUM",
        round_dp=0,
    )
    if most_steps is not None:
        records.append(most_steps)

    highest_active = _grouped_daily_pr(
        conn,
        metric_type=ACTIVE_ENERGY_TYPE,
        label="Highest active energy day",
        unit="kcal",
        aggregate="SUM",
        round_dp=0,
    )
    if highest_active is not None:
        records.append(highest_active)

    sleep_pr = _sleep_pr(conn)
    if sleep_pr is not None:
        records.append(sleep_pr)

    highest_hrv = _grouped_daily_pr(
        conn,
        metric_type=HRV_TYPE,
        label="Highest HRV (SDNN)",
        unit="ms",
        aggregate="MAX",
        round_dp=1,
    )
    if highest_hrv is not None:
        records.append(highest_hrv)

    lowest_rhr = _grouped_daily_pr(
        conn,
        metric_type=RESTING_HR_TYPE,
        label="Lowest resting HR",
        unit="bpm",
        higher_is_better=False,
        aggregate="MIN",
        round_dp=0,
    )
    if lowest_rhr is not None:
        records.append(lowest_rhr)

    # Streaks — anchored at latest_datetime(conn), bounded by `period_days`.
    latest = latest_datetime(conn)
    if latest is None:
        return PersonalRecords(
            period_days=period_days,
            records=records,
            current_streak_days=0,
            longest_streak_days=0,
            current_exercise_streak_days=0,
        )
    anchor = latest.date()
    window_start = anchor - timedelta(days=period_days)

    workout_days_window = _workout_days(conn, since=window_start)
    current_streak = _current_streak(workout_days_window, anchor)
    longest_streak = _longest_streak(workout_days_window)

    exercise_days_window = _exercise_days(conn, since=window_start)
    current_exercise_streak = _current_streak(exercise_days_window, anchor)

    return PersonalRecords(
        period_days=period_days,
        records=records,
        current_streak_days=current_streak,
        longest_streak_days=longest_streak,
        current_exercise_streak_days=current_exercise_streak,
    )
