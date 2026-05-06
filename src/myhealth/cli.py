from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from openai import OpenAIError
from rich.console import Console

from .ai import DEFAULT_CODEX_MODEL, DEFAULT_MODEL, analyze_with_ai, analyze_with_codex_oauth
from .analytics import daily_series, inspect_database, parse_period, summarize
from .apple_health import parse_export
from .auth import AUTH_PATH, clear_codex_token, get_codex_token, login_openai_codex
from .config import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH, load_config, set_config_value
from .models import PlanningStyle
from .reports import (
    print_ai_analysis,
    print_inspection,
    print_trends,
    write_ai_html_report,
    write_ai_markdown_report,
)
from .storage import clear, connect, insert_items


app = typer.Typer(help="Analyze Apple Health exports and generate strength workout plans.")
config_app = typer.Typer(help="Manage local myHealth configuration.")
auth_app = typer.Typer(help="Manage model provider authentication.")
app.add_typer(config_app, name="config")
app.add_typer(auth_app, name="auth")
console = Console()
SUPPORTED_PROVIDERS = {"openai", "openai-codex"}


def _style_from_config(style: Optional[PlanningStyle]) -> PlanningStyle:
    if style is not None:
        return style
    return PlanningStyle(load_config().get("planning_style", PlanningStyle.balanced.value))


def _provider_or_exit(provider: str) -> str:
    if provider not in SUPPORTED_PROVIDERS:
        console.print(f"Unsupported provider: {provider}. Use openai or openai-codex.")
        raise typer.Exit(1)
    return provider


def _missing_api_key_message(provider: str) -> Optional[str]:
    """Return a user-facing message if the provider needs a key that isn't set, else None."""
    import os
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        return (
            "OPENAI_API_KEY is not set. Either export OPENAI_API_KEY or use "
            "--provider openai-codex with `myhealth auth login`."
        )
    return None


def _analyze_or_exit(summary, days: int, style: PlanningStyle, model: str, provider: str):
    missing = _missing_api_key_message(provider)
    if missing:
        console.print(missing)
        raise typer.Exit(1)
    try:
        if provider == "openai-codex":
            return analyze_with_codex_oauth(summary, days, style, model)
        return analyze_with_ai(summary, days, style, model)
    except OpenAIError as exc:
        console.print(f"OpenAI request failed: {exc}")
        raise typer.Exit(1) from exc
    except RuntimeError as exc:
        console.print(f"AI analysis failed: {exc}")
        raise typer.Exit(1) from exc


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
def inspect(
    db: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database path."),
    limit: int = typer.Option(20, min=1, max=100, help="Rows to show per inventory table."),
) -> None:
    """Inspect imported Apple Health data without calling AI."""
    inventory = inspect_database(connect(db), limit=limit)
    print_inspection(inventory)


@app.command()
def trends(
    period: str = typer.Option("30d", help="Analysis period, such as 7d, 30d, or 4w."),
    db: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database path."),
) -> None:
    """Show offline health and workout trends without calling AI."""
    days = parse_period(period)
    conn = connect(db)
    summary = summarize(conn, days)
    print_trends(summary, daily_series(conn, days))


@app.command()
def report(
    period: str = typer.Option("30d", help="Analysis period, such as 7d, 30d, or 90d."),
    style: Optional[PlanningStyle] = typer.Option(None, help="Planning style override."),
    days: int = typer.Option(7, min=1, max=30, help="Number of days to include in the AI plan."),
    provider: str = typer.Option("openai-codex", help="AI provider: openai-codex (default, ChatGPT OAuth) or openai (API key)."),
    model: str = typer.Option(DEFAULT_MODEL, help="OpenAI model for analysis and planning."),
    db: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database path."),
    report_dir: Optional[Path] = typer.Option(None, help="Directory for Markdown reports."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the HTML report in your default browser."),
) -> None:
    """Ask an AI model for insights and write Markdown + HTML reports."""
    import webbrowser
    planning_style = _style_from_config(style)
    provider = _provider_or_exit(provider)
    if provider == "openai-codex" and model == DEFAULT_MODEL:
        model = DEFAULT_CODEX_MODEL
    config = load_config()
    conn = connect(db)
    summary = summarize(conn, parse_period(period))
    analysis = _analyze_or_exit(summary, days, planning_style, model, provider)
    print_ai_analysis(summary, analysis)
    output_dir = report_dir or Path(config.get("report_dir", "reports"))
    md_path = write_ai_markdown_report(summary, analysis, planning_style, model, output_dir)
    html_path = write_ai_html_report(summary, analysis, planning_style, model, output_dir, db_conn=conn)
    console.print(f"Markdown report written to {md_path}")
    console.print(f"HTML report written to {html_path}")
    if open_browser:
        # Resolve to an absolute path so as_uri() works when report_dir is relative.
        webbrowser.open(html_path.resolve().as_uri())


@app.command()
def plan(
    days: int = typer.Option(7, min=1, max=30, help="Number of days to plan."),
    period: str = typer.Option("30d", help="Data period to base the plan on."),
    style: Optional[PlanningStyle] = typer.Option(None, help="Planning style override."),
    provider: str = typer.Option("openai-codex", help="AI provider: openai-codex (default, ChatGPT OAuth) or openai (API key)."),
    model: str = typer.Option(DEFAULT_MODEL, help="OpenAI model for analysis and planning."),
    db: Path = typer.Option(DEFAULT_DB_PATH, help="SQLite database path."),
) -> None:
    """Ask an AI model for a strength-focused workout plan."""
    planning_style = _style_from_config(style)
    provider = _provider_or_exit(provider)
    if provider == "openai-codex" and model == DEFAULT_MODEL:
        model = DEFAULT_CODEX_MODEL
    summary = summarize(connect(db), parse_period(period))
    analysis = _analyze_or_exit(summary, days, planning_style, model, provider)
    print_ai_analysis(summary, analysis)


@auth_app.command("login")
def auth_login(
    provider: str = typer.Option("openai-codex", help="Provider to authenticate."),
    manual: bool = typer.Option(False, "--manual", help="Paste the redirect URL/code manually."),
) -> None:
    """Sign in to a supported OAuth provider."""
    if provider != "openai-codex":
        console.print("Only openai-codex OAuth login is currently supported.")
        raise typer.Exit(1)
    console.print("Opening browser for OpenAI Codex OAuth login...")
    token = login_openai_codex(manual=manual)
    account = token.account_id or "unknown account"
    console.print(f"OpenAI Codex OAuth login saved for {account} at {AUTH_PATH}")


@auth_app.command("status")
def auth_status() -> None:
    """Show configured model authentication."""
    token = get_codex_token()
    if token:
        expires = datetime.fromtimestamp(token.expires).astimezone().isoformat(timespec="seconds")
        console.print(
            f"openai-codex: configured, account={token.account_id or 'unknown'}, expires={expires}"
        )
    else:
        console.print("openai-codex: not configured")


@auth_app.command("logout")
def auth_logout(provider: str = typer.Option("openai-codex", help="Provider to clear.")) -> None:
    """Remove stored OAuth credentials."""
    if provider != "openai-codex":
        console.print("Only openai-codex OAuth logout is currently supported.")
        raise typer.Exit(1)
    clear_codex_token()
    console.print(f"Removed openai-codex credentials from {AUTH_PATH}")


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
