from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .models import MetricSummary, PlannedDay, PlanningStyle


def _fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.1f}{suffix}"


def print_summary(summary: MetricSummary, plan: list[PlannedDay] | None = None) -> None:
    console = Console()
    table = Table(title=f"myHealth {summary.period_days}-day summary")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Recovery", f"{summary.recovery_score}/100 ({summary.recovery_status})")
    table.add_row("Sleep avg", _fmt(summary.sleep_hours_avg, " h"))
    table.add_row("Resting HR avg", _fmt(summary.resting_hr_avg, " bpm"))
    table.add_row("HRV avg", _fmt(summary.hrv_ms_avg, " ms"))
    table.add_row("Strength sessions", str(summary.strength_sessions))
    table.add_row("Workout sessions", str(summary.workout_sessions))
    table.add_row("Active workout days", str(summary.active_days))
    console.print(table)
    for note in summary.notes:
        console.print(f"- {note}")
    if plan:
        plan_table = Table(title="Workout plan")
        plan_table.add_column("Day")
        plan_table.add_column("Recommendation")
        plan_table.add_column("Intensity")
        plan_table.add_column("Reason")
        for item in plan:
            plan_table.add_row(str(item.day), item.recommendation, item.intensity, item.reason)
        console.print(plan_table)


def write_markdown_report(
    summary: MetricSummary,
    plan: list[PlannedDay],
    style: PlanningStyle,
    report_dir: Path,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"myhealth-report-{stamp}.md"
    lines = [
        "# myHealth Report",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Period: {summary.period_days} days",
        f"Planning style: {style.value}",
        "",
        "## Summary",
        "",
        f"- Recovery: {summary.recovery_score}/100 ({summary.recovery_status})",
        f"- Average sleep: {_fmt(summary.sleep_hours_avg, ' h')}",
        f"- Average resting heart rate: {_fmt(summary.resting_hr_avg, ' bpm')}",
        f"- Average HRV: {_fmt(summary.hrv_ms_avg, ' ms')}",
        f"- Strength sessions: {summary.strength_sessions}",
        f"- Workout sessions: {summary.workout_sessions}",
        f"- Active workout days: {summary.active_days}",
        "",
        "## Insights",
        "",
    ]
    lines.extend(f"- {note}" for note in summary.notes)
    lines.extend(["", "## Plan", ""])
    for item in plan:
        lines.append(
            f"- Day {item.day}: **{item.recommendation}** ({item.intensity}) - {item.reason}"
        )
    lines.extend(
        [
            "",
            "## Disclaimer",
            "",
            "This is fitness guidance from local health data, not medical advice. Consult a qualified clinician for medical concerns.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

