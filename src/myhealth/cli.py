from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .analytics import parse_period, summarize
from .apple_health import parse_export
from .config import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH, load_config, set_config_value
from .models import PlanningStyle
from .planner import make_plan
from .reports import print_summary, write_markdown_report
from .storage import clear, connect, insert_items


app = typer.Typer(help="Analyze Apple Health exports and generate strength workout plans.")
config_app = typer.Typer(help="Manage local myHealth configuration.")
app.add_typer(config_app, name="config")
console = Console()


def _style_from_config(style: Optional[PlanningStyle]) -> PlanningStyle:
    if style is not None:
        return style
    return PlanningStyle(load_config().get("planning_style", PlanningStyle.balanced.value))


@app.command("import")
def import_export(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Apple Health export ZIP or XML."),
    append: bool = typer.Option(False, "--append", help="Append instead of replacing the local cache."),
    db: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database path."),
) -> None:
    """Import an Apple Health export into the local SQLite cache."""
    conn = connect(db)
    if not append:
        clear(conn)
    counts = insert_items(conn, parse_export(path))
    console.print(
        f"Imported {counts['records']} records and {counts['workouts']} workouts into {db}"
    )


@app.command()
def report(
    period: str = typer.Option("30d", help="Analysis period, such as 7d, 30d, or 90d."),
    style: Optional[PlanningStyle] = typer.Option(None, help="Planning style override."),
    db: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database path."),
    report_dir: Optional[Path] = typer.Option(None, help="Directory for Markdown reports."),
) -> None:
    """Print insights and write a Markdown report."""
    planning_style = _style_from_config(style)
    config = load_config()
    summary = summarize(connect(db), parse_period(period))
    plan = make_plan(summary, 7, planning_style)
    print_summary(summary, plan)
    output_dir = report_dir or Path(config.get("report_dir", "reports"))
    report_path = write_markdown_report(summary, plan, planning_style, output_dir)
    console.print(f"Markdown report written to {report_path}")


@app.command()
def plan(
    days: int = typer.Option(7, min=1, max=30, help="Number of days to plan."),
    period: str = typer.Option("30d", help="Data period to base the plan on."),
    style: Optional[PlanningStyle] = typer.Option(None, help="Planning style override."),
    db: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database path."),
) -> None:
    """Generate a strength-focused workout plan."""
    planning_style = _style_from_config(style)
    summary = summarize(connect(db), parse_period(period))
    planned_days = make_plan(summary, days, planning_style)
    print_summary(summary, planned_days)


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Set a persistent config value."""
    config = set_config_value(key, value)
    console.print(f"Saved {key}={config[key]} to {DEFAULT_CONFIG_PATH}")


@config_app.command("show")
def config_show() -> None:
    """Show current local configuration."""
    config = load_config()
    for key, value in config.items():
        console.print(f"{key} = {value}")

