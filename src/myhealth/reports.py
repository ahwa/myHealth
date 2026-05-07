from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .analytics import daily_series, personal_records
from .models import AIHealthAnalysis, HabitStreaks, MetricSummary, PersonalRecords, PlanningStyle


def _fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.1f}{suffix}"


def _fmt_int(value: float | int | None) -> str:
    return "n/a" if value is None else f"{round(value):,}"


def _fmt_iso_date(value: str | None) -> str:
    if not value:
        return "n/a"
    try:
        return datetime.fromisoformat(value).astimezone().isoformat(timespec="seconds")
    except ValueError:
        return value


def _delta_arrow(delta: float | None, lower_is_better: bool = False) -> str:
    if delta is None:
        return ""
    if delta == 0:
        return "→ 0"
    up = delta > 0
    arrow = "▲" if up else "▼"
    good = (not up) if lower_is_better else up
    color = "var(--good)" if good else "var(--bad)"
    return f'<span style="color:{color}">{arrow} {delta:+.2f}</span>'


def _delta_text(delta: float | None) -> str:
    if delta is None:
        return "n/a"
    if delta == 0:
        return "-> 0"
    arrow = "up" if delta > 0 else "down"
    return f"{arrow} {delta:+.2f}"


def print_ai_analysis(summary: MetricSummary, analysis: AIHealthAnalysis) -> None:
    console = Console()
    table = Table(title=f"myHealth {summary.period_days}-day AI analysis")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Sleep avg", _fmt(summary.sleep_hours_avg, " h"))
    table.add_row("Resting HR avg", _fmt(summary.resting_hr_avg, " bpm"))
    table.add_row("HRV avg", _fmt(summary.hrv_ms_avg, " ms"))
    table.add_row("Strength sessions", str(summary.strength_sessions))
    table.add_row("Workout sessions", str(summary.workout_sessions))
    table.add_row("Active workout days", str(summary.active_days))
    console.print(table)
    console.print(f"\n[bold]Overview[/bold]\n{analysis.overview}")
    console.print("\n[bold]Key insights[/bold]")
    for insight in analysis.key_insights:
        console.print(f"- {insight}")
    console.print(f"\n[bold]Recovery[/bold]\n{analysis.recovery_assessment}")
    plan_table = Table(title="AI workout plan")
    plan_table.add_column("Day")
    plan_table.add_column("Recommendation")
    plan_table.add_column("Intensity")
    plan_table.add_column("Reason")
    for item in analysis.workout_plan:
        plan_table.add_row(str(item.day), item.recommendation, item.intensity, item.reason)
    console.print(plan_table)
    if analysis.cautions:
        console.print("\n[bold]Cautions[/bold]")
        for caution in analysis.cautions:
            console.print(f"- {caution}")


def print_inspection(inventory: dict) -> None:
    console = Console()
    overview = Table(title="myHealth import inventory")
    overview.add_column("Item")
    overview.add_column("Value")
    overview.add_row("Records", str(inventory["records"]))
    overview.add_row("Workouts", str(inventory["workouts"]))
    overview.add_row("First data", _fmt_iso_date(inventory.get("first_date")))
    overview.add_row("Latest data", _fmt_iso_date(inventory.get("latest_date")))
    console.print(overview)

    record_table = Table(title="Top health record types")
    record_table.add_column("Type")
    record_table.add_column("Count", justify="right")
    for item in inventory["record_types"]:
        record_table.add_row(item["type"], str(item["count"]))
    if not inventory["record_types"]:
        record_table.add_row("No health records found", "0")
    console.print(record_table)

    workout_table = Table(title="Top workout types")
    workout_table.add_column("Type")
    workout_table.add_column("Count", justify="right")
    for item in inventory["workout_types"]:
        workout_table.add_row(item["type"], str(item["count"]))
    if not inventory["workout_types"]:
        workout_table.add_row("No workouts found", "0")
    console.print(workout_table)

    source_table = Table(title="Top sources")
    source_table.add_column("Source")
    source_table.add_column("Kind")
    source_table.add_column("Count", justify="right")
    for item in inventory["sources"]:
        source_table.add_row(item["source"], item["kind"], str(item["count"]))
    if not inventory["sources"]:
        source_table.add_row("No sources found", "-", "0")
    console.print(source_table)


def print_trends(summary: MetricSummary, series: dict) -> None:
    console = Console()
    overview = Table(title=f"myHealth offline trends ({summary.period_days} days)")
    overview.add_column("Metric")
    overview.add_column("Value")
    overview.add_column("Delta vs previous period")
    overview.add_row(
        "Sleep avg",
        _fmt(summary.sleep_hours_avg, " h"),
        _delta_text(summary.trend_changes.get("sleep_hours_delta")),
    )
    overview.add_row(
        "Resting HR avg",
        _fmt(summary.resting_hr_avg, " bpm"),
        _delta_text(summary.trend_changes.get("resting_hr_delta")),
    )
    overview.add_row(
        "HRV avg",
        _fmt(summary.hrv_ms_avg, " ms"),
        _delta_text(summary.trend_changes.get("hrv_ms_delta")),
    )
    overview.add_row("Recovery", f"{summary.recovery_score}/100", summary.recovery_status)
    overview.add_row(
        "Strength frequency",
        f"{summary.weekly_strength_frequency:.2f}/wk"
        if summary.weekly_strength_frequency is not None
        else "n/a",
        "",
    )
    overview.add_row("Total active time", _fmt(summary.total_active_hours, " h"), "")
    console.print(overview)

    completeness = Table(title="Data completeness")
    completeness.add_column("Signal")
    completeness.add_column("Days/Nights", justify="right")
    for key, label in (
        ("sleep_nights", "Sleep nights"),
        ("resting_hr_days", "Resting HR days"),
        ("hrv_days", "HRV days"),
        ("workout_days", "Workout days"),
    ):
        completeness.add_row(label, str(summary.data_completeness.get(key, 0)))
    console.print(completeness)

    workout_table = Table(title="Workout breakdown")
    workout_table.add_column("Type")
    workout_table.add_column("Count", justify="right")
    for name, count in sorted(summary.workouts_by_type.items(), key=lambda kv: (-kv[1], kv[0])):
        workout_table.add_row(name, str(count))
    if not summary.workouts_by_type:
        workout_table.add_row("No workouts in this period", "0")
    console.print(workout_table)

    recent = Table(title="Recent daily series", expand=True)
    recent.add_column("Date", no_wrap=True)
    recent.add_column("Sleep", justify="right", no_wrap=True)
    recent.add_column("RHR", justify="right", no_wrap=True)
    recent.add_column("HRV", justify="right", no_wrap=True)
    recent.add_column("Steps", justify="right", no_wrap=True)
    recent.add_column("Kcal", justify="right", no_wrap=True)
    recent.add_column("Ex min", justify="right", no_wrap=True)
    recent.add_column("Act h", justify="right", no_wrap=True)
    recent.add_column("Miles", justify="right", no_wrap=True)
    recent.add_column("Effort", justify="right", no_wrap=True)
    recent.add_column("WO", justify="right", no_wrap=True)
    dates = series.get("dates", [])
    start_idx = max(0, len(dates) - 14)
    for i in range(start_idx, len(dates)):
        recent.add_row(
            dates[i],
            _fmt(series["sleep_hours"][i]),
            _fmt(series["resting_hr"][i]),
            _fmt(series["hrv_ms"][i]),
            _fmt_int(series["steps"][i]),
            _fmt_int(series["active_energy_kcal"][i]),
            _fmt_int(series["exercise_minutes"][i]),
            _fmt(series["active_hours"][i]),
            _fmt(series["distance_mi"][i]),
            _fmt(series["physical_effort"][i]),
            str(series["workouts"][i]),
        )
    if not dates:
        recent.add_row("n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a", "0")
    console.print(recent)

    for note in summary.notes:
        console.print(f"- {note}")


def _fmt_pr_value(pr) -> str:
    """Render a PersonalRecord value for the terminal table."""
    if pr.unit in {"steps", "kcal"}:
        return f"{_fmt_int(pr.value)} {pr.unit}"
    # f"{v:g}" trims trailing zeros: 55.0 -> "55", 7.25 -> "7.25"
    return f"{pr.value:g} {pr.unit}"


def print_personal_records(prs: PersonalRecords) -> None:
    console = Console()

    records_table = Table(title="myHealth personal records")
    records_table.add_column("Metric")
    records_table.add_column("Value")
    records_table.add_column("Date")
    if prs.records:
        for pr in prs.records:
            records_table.add_row(pr.metric, _fmt_pr_value(pr), pr.occurred_on.isoformat())
    else:
        records_table.add_row("No personal records yet", "-", "-")
    console.print(records_table)

    streaks_table = Table(title="Streaks")
    streaks_table.add_column("Streak")
    streaks_table.add_column("Days", justify="right")
    streaks_table.add_row("Current workout streak", str(prs.current_streak_days))
    streaks_table.add_row(
        f"Longest workout streak (last {prs.period_days}d)",
        str(prs.longest_streak_days),
    )
    streaks_table.add_row("Current exercise-minutes streak", str(prs.current_exercise_streak_days))
    console.print(streaks_table)


METRIC_LABELS: dict[str, tuple[str, str]] = {
    "sleep_hours":     ("Sleep",           "h"),
    "steps":           ("Steps",           "steps"),
    "hrv_ms":          ("HRV (SDNN)",      "ms"),
    "active_calories": ("Active calories", "kcal"),
    "rhr":             ("Resting HR",      "bpm"),
}


def print_streaks(streaks: "HabitStreaks") -> None:
    """Print a Rich table of habit streaks."""
    console = Console()
    table = Table(title=f"myhealth streaks — {streaks.period_days} days")
    table.add_column("Metric")
    table.add_column("Threshold")
    table.add_column("Current", justify="right")
    table.add_column("Longest", justify="right")
    table.add_column("Last met")
    table.add_column("Status")

    if streaks.anchor_date is None:
        table.add_row("No data", "-", "-", "-", "-", "-")
        console.print(table)
        return

    for s in streaks.streaks:
        label, unit = METRIC_LABELS.get(s.metric, (s.metric, ""))
        threshold_str = f"{s.direction} {s.threshold:,.0f} {unit}"
        last_met = s.last_met_date.isoformat() if s.last_met_date is not None else "never"
        status = "[green]active[/green]" if s.active_today else "[red]broken[/red]"
        table.add_row(
            label,
            threshold_str,
            str(s.current_streak),
            str(s.longest_streak),
            last_met,
            status,
        )

    console.print(table)


def write_ai_markdown_report(
    summary: MetricSummary,
    analysis: AIHealthAnalysis,
    style: PlanningStyle,
    model: str,
    report_dir: Path,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"myhealth-ai-report-{stamp}.md"
    lines = [
        "# myHealth AI Report",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Period: {summary.period_days} days",
        f"Planning style: {style.value}",
        f"AI model: {model}",
        "",
        "## Local Metrics Sent to Model",
        "",
        f"- Average sleep: {_fmt(summary.sleep_hours_avg, ' h')}"
        + (f" (stdev {_fmt(summary.sleep_hours_stdev, ' h')})"
           if summary.sleep_hours_stdev is not None else ""),
        f"- Average resting heart rate: {_fmt(summary.resting_hr_avg, ' bpm')}"
        + (f" (RHR stdev {_fmt(summary.resting_hr_stdev, ' bpm')})"
           if summary.resting_hr_stdev is not None else ""),
        f"- Average HRV: {_fmt(summary.hrv_ms_avg, ' ms')}"
        + (f" (HRV stdev {_fmt(summary.hrv_ms_stdev, ' ms')})"
           if summary.hrv_ms_stdev is not None else ""),
        f"- Strength sessions: {summary.strength_sessions}"
        + (f" (weekly strength frequency {summary.weekly_strength_frequency:.2f}/wk)"
           if summary.weekly_strength_frequency is not None else ""),
        f"- Workout sessions: {summary.workout_sessions}",
        f"- Active workout days: {summary.active_days}",
        f"- Data completeness: {summary.data_completeness}",
        "",
        "## Overview",
        "",
        analysis.overview,
        "",
        "## Key Insights",
        "",
    ]
    lines.extend(f"- {insight}" for insight in analysis.key_insights)
    lines.extend(["", "## Recovery Assessment", "", analysis.recovery_assessment, "", "## Plan", ""])
    for item in analysis.workout_plan:
        lines.append(
            f"- Day {item.day}: **{item.recommendation}** ({item.intensity}) - {item.reason}"
        )
    if analysis.cautions:
        lines.extend(["", "## Cautions", ""])
        lines.extend(f"- {caution}" for caution in analysis.cautions)
    lines.extend(
        [
            "",
            "## Disclaimer",
            "",
            "This is AI-generated fitness guidance from summarized local health data, not medical advice. Consult a qualified clinician for medical concerns.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>myHealth AI Report — {period_days} days</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1115; --panel: #181b22; --text: #e7ebf2; --muted: #9aa3b2;
    --good: #4ade80; --bad: #f87171; --accent: #60a5fa; --warn: #fbbf24;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 32px 24px 64px; }}
  h1 {{ font-size: 28px; margin: 0 0 4px; letter-spacing: -0.5px; }}
  h2 {{ font-size: 18px; margin: 32px 0 12px; color: var(--accent); }}
  .meta {{ color: var(--muted); font-size: 13px; }}
  .grid {{ display: grid; gap: 16px;
           grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
  .card {{ background: var(--panel); border-radius: 12px; padding: 16px 18px; }}
  .kpi {{ font-size: 28px; font-weight: 600; margin-top: 4px; }}
  .kpi small {{ font-size: 13px; color: var(--muted); font-weight: 400; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px;
            font-size: 12px; font-weight: 600; }}
  .ready {{ background: #14532d; color: #86efac; }}
  .moderate {{ background: #78350f; color: #fcd34d; }}
  .recovery {{ background: #7f1d1d; color: #fca5a5; }}
  .chart-wrap {{ background: var(--panel); border-radius: 12px; padding: 16px;
                 margin-top: 12px; }}
  canvas {{ max-height: 260px; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--panel);
           border-radius: 12px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 10px 14px; border-bottom: 1px solid #262a33; }}
  th {{ background: #20242d; font-weight: 600; color: var(--muted); font-size: 13px;
        text-transform: uppercase; letter-spacing: 0.5px; }}
  tr:last-child td {{ border-bottom: none; }}
  td.day {{ font-weight: 600; color: var(--accent); white-space: nowrap; }}
  td.intensity-high {{ color: var(--bad); font-weight: 600; }}
  td.intensity-moderate {{ color: var(--warn); font-weight: 600; }}
  td.intensity-low {{ color: var(--good); font-weight: 600; }}
  ul.clean {{ padding-left: 20px; margin: 8px 0; }}
  ul.clean li {{ margin: 4px 0; }}
  .cautions {{ background: #2a1f0e; border-left: 4px solid var(--warn);
               padding: 12px 16px; border-radius: 8px; }}
  .footer {{ margin-top: 40px; color: var(--muted); font-size: 12px; }}
  .row {{ display: flex; gap: 16px; flex-wrap: wrap; align-items: baseline; }}
  .completeness {{ display: flex; gap: 14px; flex-wrap: wrap; margin-top: 6px;
                   color: var(--muted); font-size: 13px; }}
  .completeness span b {{ color: var(--text); }}
  .dual {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 6px; }}
  .dual .sub {{ font-size: 12px; color: var(--muted); text-transform: uppercase;
                letter-spacing: 0.5px; }}
  .dual .val {{ font-size: 22px; font-weight: 600; margin: 2px 0; }}
  .dual .meta {{ font-size: 12px; color: var(--muted); }}
  .types {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }}
  .types .pill {{ background: #20242d; color: var(--text); padding: 6px 10px;
                  border-radius: 999px; font-size: 13px; }}
  .types .pill b {{ color: var(--accent); margin-right: 4px; }}
</style></head>
<body><div class="wrap">

<h1>myHealth AI Report</h1>
<div class="meta">Generated {generated} · {period_days}-day window · Plan style: {style} · Model: {model}</div>

<h2>Recovery snapshot</h2>
<div class="grid">
  <div class="card">
    <div>Recovery score</div>
    <div class="kpi">{recovery_score}<small>/100</small> <span class="badge {recovery_status}">{recovery_status}</span></div>
  </div>
  <div class="card"><div>Average sleep</div><div class="kpi">{sleep_avg}<small> h</small></div>
    <div class="meta">stdev {sleep_stdev} · Δ vs prev {sleep_delta}</div></div>
  <div class="card"><div>Cardiovascular recovery</div>
    <div class="dual">
      <div>
        <div class="sub">Resting HR</div>
        <div class="val">{rhr_avg}<small style="font-size:13px;color:var(--muted)"> bpm</small></div>
        <div class="meta">stdev {rhr_stdev} · Δ {rhr_delta}</div>
      </div>
      <div>
        <div class="sub">HRV (SDNN)</div>
        <div class="val">{hrv_avg}<small style="font-size:13px;color:var(--muted)"> ms</small></div>
        <div class="meta">stdev {hrv_stdev} · Δ {hrv_delta}</div>
      </div>
    </div>
  </div>
  <div class="card"><div>Strength sessions</div><div class="kpi">{strength}<small> ({weekly_strength}/wk)</small></div></div>
  <div class="card"><div>Active days</div><div class="kpi">{active_days}<small>/{period_days}</small></div></div>
</div>

<div class="completeness">
  <span>Data completeness:</span>
  <span><b>{c_sleep}</b> sleep nights</span>
  <span><b>{c_rhr}</b> RHR days</span>
  <span><b>{c_hrv}</b> HRV days</span>
  <span><b>{c_workout}</b> workout days</span>
</div>

<h2>Workout breakdown</h2>
<div class="grid">
  <div class="card"><div>Total sessions</div><div class="kpi">{workout_sessions}</div></div>
  <div class="card"><div>Total active hours</div><div class="kpi">{total_active_hours}<small> h</small></div></div>
  <div class="card"><div>Strength sessions</div><div class="kpi">{strength}<small>/{workout_sessions}</small></div></div>
</div>
<div class="types">{workout_type_pills}</div>

{personal_records_block}

<h2>Trends ({period_days} days)</h2>
<div class="chart-wrap"><canvas id="ch_sleep"></canvas></div>
<div class="chart-wrap"><canvas id="ch_rhr"></canvas></div>
<div class="chart-wrap"><canvas id="ch_hrv"></canvas></div>
<div class="chart-wrap"><canvas id="ch_workouts"></canvas></div>

<h2>AI overview</h2>
<div class="card"><p>{overview}</p></div>

<h2>Key insights</h2>
<div class="card"><ul class="clean">{insights_html}</ul></div>

<h2>Recovery assessment</h2>
<div class="card"><p>{recovery_assessment}</p></div>

<h2>Workout plan</h2>
<table>
  <thead><tr><th>Day</th><th>Recommendation</th><th>Intensity</th><th>Reason</th></tr></thead>
  <tbody>{plan_rows}</tbody>
</table>

{cautions_block}

<div class="footer">This is AI-generated fitness guidance from summarized local health data — not medical advice. Consult a qualified clinician for medical concerns.</div>
</div>

<script>
const series = {series_json};
const baseOpts = (label, color) => ({{
  type: 'line',
  data: {{ labels: series.dates,
           datasets: [{{ label, data: [], borderColor: color, backgroundColor: color+'33',
                         spanGaps: true, tension: 0.25, pointRadius: 2 }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
              plugins: {{ legend: {{ labels: {{ color: '#e7ebf2' }} }} }},
              scales: {{ x: {{ ticks: {{ color: '#9aa3b2', maxRotation: 0, autoSkip: true }} }},
                         y: {{ ticks: {{ color: '#9aa3b2' }}, grid: {{ color: '#262a33' }} }} }} }}
}});
const make = (id, label, color, data, type) => {{
  const cfg = baseOpts(label, color);
  cfg.data.datasets[0].data = data;
  if (type) cfg.type = type;
  if (type === 'bar') {{ cfg.data.datasets[0].backgroundColor = color; }}
  new Chart(document.getElementById(id), cfg);
}};
make('ch_sleep', 'Sleep hours', '#60a5fa', series.sleep_hours);
make('ch_rhr', 'Resting HR (bpm)', '#f87171', series.resting_hr);
make('ch_hrv', 'HRV SDNN (ms)', '#4ade80', series.hrv_ms);
make('ch_workouts', 'Workouts per day', '#fbbf24', series.workouts, 'bar');
</script>
</body></html>"""


def write_ai_html_report(
    summary: MetricSummary,
    analysis: AIHealthAnalysis,
    style: PlanningStyle,
    model: str,
    report_dir: Path,
    db_conn: sqlite3.Connection | None = None,
) -> Path:
    """Render a self-contained HTML report with Chart.js trends + a plan table."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"myhealth-ai-report-{stamp}.html"

    # Daily series for charts. Empty arrays if no DB connection was provided.
    if db_conn is not None:
        series = daily_series(db_conn, summary.period_days)
        prs = personal_records(db_conn, summary.period_days)
        if prs.records:
            pr_pills = "".join(
                f'<span class="pill"><b>{_fmt_pr_value(pr)}</b> '
                f'{html.escape(pr.metric)} · {pr.occurred_on.isoformat()}</span>'
                for pr in prs.records
            )
        else:
            pr_pills = '<span class="meta">No personal records in this DB.</span>'
        personal_records_block = (
            "<h2>Personal records</h2>\n"
            "<div class=\"grid\">\n"
            f"  <div class=\"card\"><div>Current workout streak</div>"
            f"<div class=\"kpi\">{prs.current_streak_days}<small> days</small></div></div>\n"
            f"  <div class=\"card\"><div>Longest streak (last {prs.period_days}d)</div>"
            f"<div class=\"kpi\">{prs.longest_streak_days}<small> days</small></div></div>\n"
            f"  <div class=\"card\"><div>Exercise-minutes streak</div>"
            f"<div class=\"kpi\">{prs.current_exercise_streak_days}<small> days</small></div></div>\n"
            "</div>\n"
            f"<div class=\"types\">{pr_pills}</div>"
        )
    else:
        series = {"dates": [], "sleep_hours": [], "resting_hr": [], "hrv_ms": [], "workouts": []}
        personal_records_block = ""

    insights_html = "".join(f"<li>{html.escape(i)}</li>" for i in analysis.key_insights) or "<li>None</li>"

    plan_rows = []
    for item in analysis.workout_plan:
        intensity = (item.intensity or "").lower()
        cls = f"intensity-{intensity}" if intensity in {"high", "moderate", "low"} else ""
        plan_rows.append(
            f"<tr><td class='day'>Day {item.day}</td>"
            f"<td>{html.escape(item.recommendation)}</td>"
            f"<td class='{cls}'>{html.escape(item.intensity)}</td>"
            f"<td>{html.escape(item.reason)}</td></tr>"
        )

    cautions_block = ""
    if analysis.cautions:
        items = "".join(f"<li>{html.escape(c)}</li>" for c in analysis.cautions)
        cautions_block = f"<h2>Cautions</h2><div class='cautions'><ul class='clean'>{items}</ul></div>"

    # Sort workouts by count desc for display.
    types_sorted = sorted(
        (summary.workouts_by_type or {}).items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    workout_type_pills = "".join(
        f'<span class="pill"><b>{count}</b>{html.escape(name)}</span>'
        for name, count in types_sorted
    ) or '<span class="meta">No workouts in this window.</span>'

    rendered = _HTML_TEMPLATE.format(
        period_days=summary.period_days,
        generated=html.escape(datetime.now().astimezone().isoformat(timespec="seconds")),
        style=html.escape(style.value),
        model=html.escape(model),
        recovery_score=summary.recovery_score,
        recovery_status=html.escape(summary.recovery_status),
        sleep_avg=_fmt(summary.sleep_hours_avg),
        sleep_stdev=_fmt(summary.sleep_hours_stdev),
        sleep_delta=_delta_arrow(summary.trend_changes.get("sleep_hours_delta")),
        rhr_avg=_fmt(summary.resting_hr_avg),
        rhr_stdev=_fmt(summary.resting_hr_stdev),
        rhr_delta=_delta_arrow(summary.trend_changes.get("resting_hr_delta"), lower_is_better=True),
        hrv_avg=_fmt(summary.hrv_ms_avg),
        hrv_stdev=_fmt(summary.hrv_ms_stdev),
        hrv_delta=_delta_arrow(summary.trend_changes.get("hrv_ms_delta")),
        strength=summary.strength_sessions,
        weekly_strength=(f"{summary.weekly_strength_frequency:.2f}"
                        if summary.weekly_strength_frequency is not None else "n/a"),
        active_days=summary.active_days,
        workout_sessions=summary.workout_sessions,
        total_active_hours=(f"{summary.total_active_hours:.1f}"
                            if summary.total_active_hours is not None else "n/a"),
        workout_type_pills=workout_type_pills,
        personal_records_block=personal_records_block,
        c_sleep=summary.data_completeness.get("sleep_nights", 0),
        c_rhr=summary.data_completeness.get("resting_hr_days", 0),
        c_hrv=summary.data_completeness.get("hrv_days", 0),
        c_workout=summary.data_completeness.get("workout_days", 0),
        overview=html.escape(analysis.overview).replace("\n", "<br>"),
        insights_html=insights_html,
        recovery_assessment=html.escape(analysis.recovery_assessment).replace("\n", "<br>"),
        plan_rows="".join(plan_rows) or "<tr><td colspan='4'>No plan returned</td></tr>",
        cautions_block=cautions_block,
        series_json=json.dumps(series),
    )
    path.write_text(rendered, encoding="utf-8")
    return path
