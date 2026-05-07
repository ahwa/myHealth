from pathlib import Path

from myhealth.analytics import daily_series, inspect_database, parse_period, summarize
from myhealth.ai import build_analysis_prompt
from myhealth.auth import CODEX_CLIENT_ID, CODEX_REDIRECT_HOST, _authorization_url, _code_from_pasted_value
from myhealth.apple_health import parse_export
from myhealth.models import AIHealthAnalysis, PlannedDay, PlanningStyle
from myhealth.reports import write_ai_markdown_report
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


def test_summary_includes_variability_stdev(tmp_path):
    """Sample stdev of RHR (58, 55) and HRV (48, 54), and None for single-night sleep."""
    import math
    conn = connect(tmp_path / "health.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    summary = summarize(conn, parse_period("30d"))

    # Sample stdev of [58, 55] = sqrt(((58-56.5)^2 + (55-56.5)^2) / 1) = sqrt(4.5)
    assert summary.resting_hr_stdev is not None
    assert math.isclose(summary.resting_hr_stdev, math.sqrt(4.5), rel_tol=1e-6)
    # Sample stdev of [48, 54] = sqrt(18)
    assert summary.hrv_ms_stdev is not None
    assert math.isclose(summary.hrv_ms_stdev, math.sqrt(18.0), rel_tol=1e-6)
    # Only one sleep night -> stdev undefined
    assert summary.sleep_hours_stdev is None


def test_summary_data_completeness_counts_distinct_days(tmp_path):
    """data_completeness reports how many distinct days have each metric, by metric key."""
    conn = connect(tmp_path / "health.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    summary = summarize(conn, parse_period("30d"))

    assert summary.data_completeness == {
        "sleep_nights": 1,        # one Asleep* record
        "resting_hr_days": 2,     # two distinct days
        "hrv_days": 2,
        "workout_days": 2,
    }


def test_markdown_report_renders_variability_and_completeness(tmp_path):
    """The Markdown report should expose the new metrics so the user sees what the AI saw."""
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)

    analysis = AIHealthAnalysis(
        overview="ok",
        key_insights=["i"],
        recovery_assessment="r",
        workout_plan=[PlannedDay(day=1, recommendation="x", intensity="low", reason="r")],
        cautions=[],
    )

    path = write_ai_markdown_report(summary, analysis, PlanningStyle.balanced, "gpt-test", tmp_path)
    text = path.read_text()

    assert "RHR stdev" in text
    assert "HRV stdev" in text
    assert "Data completeness" in text
    assert "weekly strength frequency" in text.lower()


def test_summary_weekly_strength_frequency(tmp_path):
    """Strength sessions per 7 days over the analysis window."""
    import math
    conn = connect(tmp_path / "health.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    summary = summarize(conn, parse_period("30d"))

    # 2 strength sessions over 30 days = 0.4666...
    expected = 2 * 7 / 30
    assert summary.weekly_strength_frequency is not None
    assert math.isclose(summary.weekly_strength_frequency, expected, rel_tol=1e-6)


def test_inspect_database_reports_inventory(tmp_path):
    conn = connect(tmp_path / "health.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    inventory = inspect_database(conn, limit=5)

    assert inventory["records"] == 5
    assert inventory["workouts"] == 2
    assert inventory["first_date"] is not None
    assert inventory["latest_date"] is not None
    assert any(item["type"] == "RestingHeartRate" for item in inventory["record_types"])
    assert any(item["type"] == "TraditionalStrengthTraining" for item in inventory["workout_types"])
    assert any(item["source"] == "Apple Watch" for item in inventory["sources"])


def test_inspect_command_prints_inventory(tmp_path, capsys):
    from myhealth import cli as cli_mod

    db = tmp_path / "health.sqlite"
    conn = connect(db)
    insert_items(conn, parse_export(FIXTURE))

    cli_mod.inspect(db=db, limit=5)
    output = capsys.readouterr().out

    assert "myHealth import inventory" in output
    assert "Records" in output
    assert "TraditionalStrengthTraining" in output


def test_trends_command_prints_offline_summary(tmp_path, capsys):
    from myhealth import cli as cli_mod

    db = tmp_path / "health.sqlite"
    conn = connect(db)
    insert_items(conn, parse_export(FIXTURE))

    cli_mod.trends(period="30d", db=db)
    output = capsys.readouterr().out

    assert "offline trends" in output
    assert "Sleep avg" in output
    assert "Workout breakdown" in output
    assert "Recent daily series" in output
    assert "TraditionalStrengthTraining" in output


def test_ai_prompt_uses_summary_not_raw_records():
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)

    prompt = build_analysis_prompt(summary, 7, PlanningStyle.balanced)

    assert "average_sleep_hours" in prompt
    assert "HKQuantityTypeIdentifierRestingHeartRate" not in prompt
    assert "export.xml" not in prompt


def test_ai_prompt_includes_variability_and_completeness():
    """The AI must see stdev + data_completeness so it can weight averages."""
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)

    prompt = build_analysis_prompt(summary, 7, PlanningStyle.balanced)

    assert "resting_hr_stdev_bpm" in prompt
    assert "hrv_sdnn_stdev_ms" in prompt
    assert "data_completeness" in prompt
    assert "weekly_strength_frequency" in prompt


def test_codex_oauth_authorization_url_uses_pkce():
    url = _authorization_url("challenge", "state", "http://localhost:9999/auth/callback")

    assert "auth.openai.com/oauth/authorize" in url
    assert f"client_id={CODEX_CLIENT_ID}" in url
    assert "code_challenge=challenge" in url
    assert "code_challenge_method=S256" in url


def test_codex_oauth_redirect_uri_uses_localhost():
    """redirect_uri must use localhost (not 127.0.0.1) to match OpenAI's registered app."""
    assert CODEX_REDIRECT_HOST == "localhost"
    url = _authorization_url("challenge", "state", f"http://{CODEX_REDIRECT_HOST}:9999/auth/callback")
    assert "localhost" in url
    assert "127.0.0.1" not in url


def test_codex_oauth_pasted_redirect_parsing():
    code, state = _code_from_pasted_value(
        "http://127.0.0.1:1455/auth/callback?code=abc123&state=xyz"
    )

    assert code == "abc123"
    assert state == "xyz"


# --- End-to-end smoke: `myhealth plan --provider openai-codex` happy path ---

def test_daily_series_returns_one_entry_per_day_in_window(tmp_path):
    """daily_series(conn, period_days) returns aligned per-day arrays."""
    from myhealth.analytics import daily_series
    conn = connect(tmp_path / "h.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    series = daily_series(conn, 30)

    # Aligned arrays of equal length with one entry per day in the window
    n = len(series["dates"])
    assert n == 30
    assert len(series["sleep_hours"]) == n
    assert len(series["resting_hr"]) == n
    assert len(series["hrv_ms"]) == n
    assert len(series["steps"]) == n
    assert len(series["active_energy_kcal"]) == n
    assert len(series["exercise_minutes"]) == n
    assert len(series["active_hours"]) == n
    assert len(series["distance_mi"]) == n
    assert len(series["physical_effort"]) == n
    assert len(series["workouts"]) == n
    # The fixture's RHR readings of 58 and 55 should appear somewhere as numbers
    rhr = [v for v in series["resting_hr"] if v is not None]
    assert 58.0 in rhr and 55.0 in rhr
    # Days with no workout produce 0; the two fixture workout days produce 1
    assert max(series["workouts"]) == 1
    assert series["workouts"].count(1) == 2


def test_report_command_handles_relative_report_dir(tmp_path, monkeypatch):
    """Regression: relative report_dir ('reports/') must still produce a file:// URI."""
    import json, os, urllib.request
    from myhealth import ai as ai_mod
    from myhealth import cli as cli_mod
    from myhealth.auth import CodexToken

    class FakeResponse:
        def __init__(self, lines): self._lines = [l.encode() for l in lines]
        def __iter__(self): return iter(self._lines)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        completed = {"type": "response.completed", "response": {"output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                "overview": "ok", "key_insights": ["i"], "recovery_assessment": "r",
                "workout_plan": [{"day": 1, "recommendation": "x",
                                  "intensity": "low", "reason": "r"}],
                "cautions": []})}]}]}}
        return FakeResponse([f"data: {json.dumps(completed)}\n", "data: [DONE]\n"])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ai_mod, "get_valid_codex_token",
        lambda: CodexToken(access="t", refresh="r", expires=9999, account_id="a"))
    monkeypatch.setattr(cli_mod, "load_config",
                        lambda: {"planning_style": "balanced", "report_dir": "reports"})

    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)

    db = tmp_path / "h.sqlite"
    conn = connect(db); insert_items(conn, parse_export(FIXTURE)); conn.commit(); conn.close()

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        cli_mod.report(period="30d", style=None, days=7,
                       provider="openai-codex", model=ai_mod.DEFAULT_CODEX_MODEL,
                       db=db,
                       report_dir=None,  # falls back to config "reports"  -> RELATIVE
                       open_browser=True)
    finally:
        os.chdir(cwd)

    assert opened, "webbrowser.open was not called"
    assert opened[0].startswith("file://"), f"not a file URI: {opened[0]}"
    # And the URI is absolute
    assert "://" in opened[0] and not opened[0].startswith("file://reports/")


def test_report_command_opens_html_in_browser(tmp_path, monkeypatch):
    """`myhealth report` should write an HTML file and open it via webbrowser.open."""
    import json, urllib.request
    from myhealth import ai as ai_mod
    from myhealth import cli as cli_mod
    from myhealth.auth import CodexToken

    class FakeResponse:
        def __init__(self, lines): self._lines = [l.encode() for l in lines]
        def __iter__(self): return iter(self._lines)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        completed = {"type": "response.completed", "response": {"output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                "overview": "ok", "key_insights": ["i"],
                "recovery_assessment": "r",
                "workout_plan": [{"day": 1, "recommendation": "x",
                                  "intensity": "low", "reason": "r"}],
                "cautions": []})}]}]}}
        return FakeResponse([f"data: {json.dumps(completed)}\n", "data: [DONE]\n"])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ai_mod, "get_valid_codex_token",
        lambda: CodexToken(access="t", refresh="r", expires=9999, account_id="a"))
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"planning_style": "balanced"})

    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)

    db = tmp_path / "h.sqlite"
    conn = connect(db); insert_items(conn, parse_export(FIXTURE)); conn.commit(); conn.close()

    cli_mod.report(period="30d", style=None, days=7,
                   provider="openai-codex", model=ai_mod.DEFAULT_CODEX_MODEL,
                   db=db, report_dir=tmp_path)

    htmls = list(tmp_path.glob("myhealth-ai-report-*.html"))
    assert len(htmls) == 1, "expected exactly one HTML report to be written"
    assert opened and opened[0].startswith("file://"), f"webbrowser.open not called with file:// URL: {opened}"
    assert htmls[0].as_uri() == opened[0]


def test_summary_exposes_workout_breakdown_and_active_hours(tmp_path):
    """MetricSummary should report workouts_by_type and total_active_hours."""
    conn = connect(tmp_path / "h.sqlite")
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)

    # Fixture: 2 workouts, one Traditional + one Functional strength training,
    # 45 min and 40 min respectively.
    assert summary.total_active_hours is not None
    assert abs(summary.total_active_hours - (45 + 40) / 60) < 0.005
    # The HK prefix should be stripped for readability.
    assert summary.workouts_by_type == {
        "TraditionalStrengthTraining": 1,
        "FunctionalStrengthTraining": 1,
    }


def test_ai_prompt_includes_workout_breakdown():
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)

    prompt = build_analysis_prompt(summary, 7, PlanningStyle.balanced)

    assert "workouts_by_type" in prompt
    assert "total_active_hours" in prompt
    assert "TraditionalStrengthTraining" in prompt


def test_html_report_combines_rhr_hrv_and_shows_workout_breakdown(tmp_path):
    """RHR and HRV must share one card; workout breakdown section must list types."""
    from myhealth.reports import write_ai_html_report
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)
    analysis = AIHealthAnalysis(
        overview="x", key_insights=["y"], recovery_assessment="z",
        workout_plan=[PlannedDay(day=1, recommendation="r", intensity="low", reason="r")],
        cautions=[],
    )
    path = write_ai_html_report(summary, analysis, PlanningStyle.balanced,
                                "gpt-test", tmp_path, db_conn=conn)
    text = path.read_text()

    # Combined card title
    assert "Cardiovascular recovery" in text
    # Both metrics still surfaced under the combined card
    assert "Resting HR" in text and "HRV" in text
    # The old standalone Resting-HR / HRV KPI cards are gone — the values now
    # live inside a `.dual` block instead.
    import re
    standalone_rhr = re.search(r'<div>Resting HR</div>\s*<div class="kpi">', text)
    standalone_hrv = re.search(r'<div>HRV[^<]*</div>\s*<div class="kpi">', text)
    assert standalone_rhr is None, "Resting HR still rendered as its own KPI card"
    assert standalone_hrv is None, "HRV still rendered as its own KPI card"
    assert 'class="dual"' in text

    # Workout breakdown section
    assert "Workout breakdown" in text
    assert "TraditionalStrengthTraining" in text
    assert "FunctionalStrengthTraining" in text
    # Total active hours rendered (45+40)/60 = 1.42 h
    assert "1.4" in text  # one decimal place is enough


def test_html_report_contains_charts_and_plan_table(tmp_path):
    """write_ai_html_report should embed Chart.js datasets and a plan table."""
    from myhealth.reports import write_ai_html_report
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)
    analysis = AIHealthAnalysis(
        overview="Test overview", key_insights=["insight one"],
        recovery_assessment="moderate",
        workout_plan=[
            PlannedDay(day=1, recommendation="Heavy strength",
                       intensity="high", reason="rested"),
            PlannedDay(day=2, recommendation="Recovery",
                       intensity="low", reason="fatigue"),
        ],
        cautions=["watch RHR"],
    )

    path = write_ai_html_report(summary, analysis, PlanningStyle.balanced,
                                "gpt-test", tmp_path)
    text = path.read_text()

    assert path.suffix == ".html"
    # Chart.js loaded
    assert "chart.js" in text.lower()
    # Real datasets present (sleep / RHR / HRV / workouts)
    assert "Sleep hours" in text
    assert "Resting HR" in text
    assert "HRV" in text
    # Plan rendered as a table with both days
    assert "<table" in text
    assert "Heavy strength" in text and "Recovery" in text
    assert "Day 1" in text and "Day 2" in text
    # Recovery score and AI overview are visible
    assert str(summary.recovery_score) in text
    assert "Test overview" in text
    # Cautions surfaced
    assert "watch RHR" in text


def test_daily_series_handles_empty_db(tmp_path):
    from myhealth.analytics import daily_series
    conn = connect(tmp_path / "h.sqlite")
    series = daily_series(conn, 7)
    assert len(series["dates"]) == 7
    assert all(v is None for v in series["sleep_hours"])
    assert all(v is None for v in series["steps"])
    assert all(v == 0 for v in series["active_hours"])
    assert all(v == 0 for v in series["workouts"])


def test_report_and_plan_default_to_openai_codex_provider():
    """Bare `myhealth report` and `myhealth plan` should use the OAuth provider."""
    import inspect
    from myhealth.cli import report, plan
    for fn in (report, plan):
        sig = inspect.signature(fn)
        raw = sig.parameters["provider"].default
        # Real typer wraps in OptionInfo with .default; the shim returns bare value.
        actual = getattr(raw, "default", raw)
        assert actual == "openai-codex", (
            f"{fn.__name__} provider default should be openai-codex, got {actual!r}"
        )


def test_codex_model_not_supported_error_lists_fallbacks(monkeypatch):
    """A 400 'not supported' from the backend should suggest fallback models."""
    import io, json, urllib.error, urllib.request
    from myhealth import ai as ai_mod
    from myhealth.auth import CodexToken

    def fake_urlopen(req, timeout=None):
        body = b'{"detail":"The \'gpt-5.4-mini\' model is not supported when using Codex with a ChatGPT account."}'
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        ai_mod, "get_valid_codex_token",
        lambda: CodexToken(access="t", refresh="r", expires=9999, account_id="a"),
    )
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)

    import pytest
    with pytest.raises(RuntimeError) as exc_info:
        ai_mod.analyze_with_codex_oauth(summary, 7, PlanningStyle.balanced, "gpt-5.4-mini")
    msg = str(exc_info.value)
    assert "not supported" in msg
    # The hint should mention at least one fallback that's NOT the failing model
    assert "gpt-5.5" in msg or "gpt-5.3-codex-spark" in msg
    assert "myhealth plan --provider openai-codex --model" in msg


def test_plan_openai_codex_happy_path(tmp_path, monkeypatch):
    """Exercise the full plan() path with mocked OAuth + mocked HTTP response.

    Verifies the request we send to Codex has the right URL, model, headers,
    and structured input shape, and that the SSE response is parsed back into
    an AIHealthAnalysis without errors.
    """
    import io, json, urllib.request
    from myhealth import ai as ai_mod
    from myhealth import cli as cli_mod
    from myhealth.auth import CodexToken

    captured = {}

    class FakeResponse:
        def __init__(self, lines):
            self._lines = [l.encode("utf-8") for l in lines]
        def __iter__(self): return iter(self._lines)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        completed = {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({
                                    "overview": "ok",
                                    "key_insights": ["sleep is steady"],
                                    "recovery_assessment": "moderate",
                                    "workout_plan": [
                                        {"day": 1, "recommendation": "Strength",
                                         "intensity": "moderate", "reason": "fresh"}
                                    ],
                                    "cautions": [],
                                }),
                            }
                        ],
                    }
                ]
            },
        }
        return FakeResponse([f"data: {json.dumps(completed)}\n", "data: [DONE]\n"])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        ai_mod, "get_valid_codex_token",
        lambda: CodexToken(access="atok", refresh="r", expires=9999, account_id="acct-1"),
    )

    # Set up a fresh DB with the fixture so summarize() returns a real summary
    db = tmp_path / "h.sqlite"
    conn = connect(db)
    insert_items(conn, parse_export(FIXTURE))
    conn.commit()
    conn.close()

    # Drive the same call sequence the `plan` CLI command performs
    from myhealth.analytics import parse_period as _pp, summarize as _sum
    from myhealth.storage import connect as _conn
    summary = _sum(_conn(db), _pp("30d"))
    analysis = cli_mod._analyze_or_exit(
        summary, days=7, style=PlanningStyle.balanced,
        model=ai_mod.DEFAULT_CODEX_MODEL, provider="openai-codex",
    )

    # Request shape sanity checks
    assert captured["url"] == ai_mod.CODEX_RESPONSES_URL
    assert captured["body"]["model"] == ai_mod.DEFAULT_CODEX_MODEL
    assert isinstance(captured["body"]["input"], list)
    assert captured["body"]["input"][0]["role"] == "user"
    h = {k.lower(): v for k, v in captured["headers"].items()}
    assert h["authorization"] == "Bearer atok"
    assert h["chatgpt-account-id"] == "acct-1"
    assert h["openai-beta"] == "responses=v1"
    assert h["accept"] == "text/event-stream"

    # Response was parsed into our structured model
    assert analysis.overview == "ok"
    first = analysis.workout_plan[0]
    day = first.day if hasattr(first, "day") else first["day"]
    assert day == 1


# --- Round 3: trend deltas + empty-DB handling ---

def _build_records_xml(records, workouts=()):
    """Helper: build a minimal Apple Health export XML with the given records/workouts."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!DOCTYPE HealthData>',
             '<HealthData locale="en_US">']
    for r in records:
        parts.append(
            f'<Record type="{r["type"]}" sourceName="t" unit="{r.get("unit","")}" '
            f'creationDate="{r["start"]}" startDate="{r["start"]}" endDate="{r["end"]}" '
            f'value="{r["value"]}"/>'
        )
    for w in workouts:
        parts.append(
            f'<Workout workoutActivityType="{w["type"]}" duration="{w["duration"]}" '
            f'durationUnit="min" sourceName="t" startDate="{w["start"]}" endDate="{w["end"]}" '
            f'totalEnergyBurned="100" totalEnergyBurnedUnit="kcal"/>'
        )
    parts.append('</HealthData>')
    return "\n".join(parts)


def test_sleep_average_buckets_by_night_not_by_segment(tmp_path):
    """Apple Health stores sleep as many short Asleep* segments per night.

    The summary must aggregate segments per night (sum), then average across
    nights — not average across segments (which would land near 0.3 h).
    """
    # Two nights of sleep, recorded as multiple short segments each.
    # Night 1 (waking 2026-04-30): 23:00->02:00 + 02:30->06:00 = 6.5h
    # Night 2 (waking 2026-05-01): 22:00->06:00 (single segment) = 8.0h
    sleep_segs = [
        {"type": "HKCategoryTypeIdentifierSleepAnalysis",
         "start": "2026-04-29 23:00:00 -0700", "end": "2026-04-30 02:00:00 -0700",
         "value": "HKCategoryValueSleepAnalysisAsleepCore"},
        {"type": "HKCategoryTypeIdentifierSleepAnalysis",
         "start": "2026-04-30 02:30:00 -0700", "end": "2026-04-30 06:00:00 -0700",
         "value": "HKCategoryValueSleepAnalysisAsleepDeep"},
        {"type": "HKCategoryTypeIdentifierSleepAnalysis",
         "start": "2026-04-30 22:00:00 -0700", "end": "2026-05-01 06:00:00 -0700",
         "value": "HKCategoryValueSleepAnalysisAsleepREM"},
    ]
    xml = _build_records_xml(sleep_segs, workouts=[
        # Need at least one record/workout so latest_datetime is deterministic.
        {"type": "HKWorkoutActivityTypeWalking",
         "start": "2026-05-01 12:00:00 -0700", "end": "2026-05-01 12:30:00 -0700",
         "duration": 30}
    ])
    fixture = tmp_path / "sleep.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    summary = summarize(conn, 7)

    # Two distinct nights of sleep, not three segments
    assert summary.data_completeness["sleep_nights"] == 2
    # (6.5 + 8.0) / 2 = 7.25 h
    assert summary.sleep_hours_avg is not None
    assert abs(summary.sleep_hours_avg - 7.25) < 0.01, (
        f"expected ~7.25h average, got {summary.sleep_hours_avg}"
    )


def test_summary_exposes_period_over_period_trend(tmp_path):
    """Comparing the current period to the previous period should surface a trend dict."""
    # Build 2 periods of RHR: previous 60 bpm avg, current 50 bpm avg
    xml = _build_records_xml([
        # previous window: 2026-04-01 .. 2026-04-08
        {"type": "HKQuantityTypeIdentifierRestingHeartRate", "unit": "count/min",
         "start": "2026-04-02 08:00:00 -0700", "end": "2026-04-02 08:00:00 -0700", "value": 60},
        {"type": "HKQuantityTypeIdentifierRestingHeartRate", "unit": "count/min",
         "start": "2026-04-05 08:00:00 -0700", "end": "2026-04-05 08:00:00 -0700", "value": 60},
        # current window: 2026-04-09 .. 2026-04-16
        {"type": "HKQuantityTypeIdentifierRestingHeartRate", "unit": "count/min",
         "start": "2026-04-10 08:00:00 -0700", "end": "2026-04-10 08:00:00 -0700", "value": 50},
        {"type": "HKQuantityTypeIdentifierRestingHeartRate", "unit": "count/min",
         "start": "2026-04-15 08:00:00 -0700", "end": "2026-04-15 08:00:00 -0700", "value": 50},
    ])
    fixture = tmp_path / "synthetic.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    summary = summarize(conn, 7)

    assert "resting_hr_delta" in summary.trend_changes
    # current 50 - previous 60 = -10 bpm
    assert summary.trend_changes["resting_hr_delta"] == -10.0


def test_ai_prompt_includes_trend_changes():
    """Trend deltas vs the prior period must reach the model."""
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)
    summary.trend_changes = {"resting_hr_delta": -3.0}

    prompt = build_analysis_prompt(summary, 7, PlanningStyle.balanced)

    assert "trend_changes" in prompt
    assert "resting_hr_delta" in prompt


def test_summary_flags_no_data_clearly(tmp_path):
    """An empty DB should yield zeroed metrics and a clear 'no data' note."""
    conn = connect(":memory:")  # type: ignore[arg-type]

    summary = summarize(conn, 30)

    assert summary.sleep_hours_avg is None
    assert summary.resting_hr_avg is None
    assert summary.workout_sessions == 0
    assert any("no data" in n.lower() for n in summary.notes)


# --- Round 2: code review fixes ---

def test_parse_period_accepts_weeks():
    assert parse_period("4w") == 28
    assert parse_period("1w") == 7


def test_parse_period_rejects_invalid_units():
    import pytest
    with pytest.raises(ValueError):
        parse_period("4m")
    with pytest.raises(ValueError):
        parse_period("0d")


def test_ai_prompt_includes_summary_notes():
    """Computed recovery notes should reach the model so it sees the same signals."""
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)
    summary.notes = ["TEST_MARKER: low HRV trend"]

    prompt = build_analysis_prompt(summary, 7, PlanningStyle.balanced)

    assert "TEST_MARKER: low HRV trend" in prompt


def test_dead_print_summary_helpers_removed():
    """Legacy non-AI rendering helpers should be deleted now that planner is gone."""
    from myhealth import reports
    assert not hasattr(reports, "print_summary")
    assert not hasattr(reports, "write_markdown_report")


def test_missing_api_key_message_is_explicit():
    """The CLI helper should detect a missing key before sending the request."""
    import os
    from myhealth.cli import _missing_api_key_message
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        msg = _missing_api_key_message("openai")
        assert msg is not None
        assert "OPENAI_API_KEY" in msg
        # OAuth provider doesn't need the env var
        assert _missing_api_key_message("openai-codex") is None
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved


# --- Security fix tests ---

def test_state_mismatch_raises_even_when_auth_state_is_none():
    """Bare pasted code (state=None) must still fail the state check."""
    from myhealth.auth import _check_state
    import pytest

    with pytest.raises(RuntimeError, match="state"):
        _check_state(received=None, expected="expected-state")


def test_state_match_passes():
    from myhealth.auth import _check_state

    _check_state(received="abc", expected="abc")  # should not raise


def test_store_token_is_atomic(tmp_path):
    """Token write should not leave a partial file if interrupted."""
    import json
    from myhealth.auth import _store_token, CodexToken

    token = CodexToken(access="a", refresh="r", expires=9999)
    path = tmp_path / "auth.json"
    _store_token(token, path)
    data = json.loads(path.read_text())
    assert data["openai-codex"]["access"] == "a"
    assert path.stat().st_mode & 0o777 == 0o600


def test_json_post_raises_on_http_error():
    """HTTP errors from token endpoint expose only error/error_description, not raw body."""
    import urllib.error
    import urllib.request
    from unittest.mock import patch
    from myhealth.auth import _json_post

    body = b'{"error": "invalid_grant", "error_description": "Token expired"}'
    http_error = urllib.error.HTTPError(
        url="https://example.com", code=400, msg="Bad Request",
        hdrs={}, fp=__import__("io").BytesIO(body)  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        import pytest
        with pytest.raises(RuntimeError, match="invalid_grant"):
            _json_post("https://example.com", {"grant_type": "refresh_token"})


# --- Personal records & streaks ---

def test_personal_records_includes_longest_workout_from_fixture(tmp_path):
    from datetime import date
    from myhealth.analytics import personal_records

    conn = connect(tmp_path / "health.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    prs = personal_records(conn)

    longest = next((r for r in prs.records if r.metric == "Longest workout"), None)
    assert longest is not None
    assert longest.value == 45.0
    assert longest.unit == "min"
    assert longest.occurred_on == date(2026, 5, 1)
    assert longest.higher_is_better is True


def test_personal_records_includes_longest_sleep_bucketed_by_wake_date(tmp_path):
    from datetime import date
    from myhealth.analytics import personal_records

    # Two nights: 6.5h waking 2026-04-30, 8.0h waking 2026-05-01
    sleep_segs = [
        {"type": "HKCategoryTypeIdentifierSleepAnalysis",
         "start": "2026-04-29 23:00:00 -0700", "end": "2026-04-30 02:00:00 -0700",
         "value": "HKCategoryValueSleepAnalysisAsleepCore"},
        {"type": "HKCategoryTypeIdentifierSleepAnalysis",
         "start": "2026-04-30 02:30:00 -0700", "end": "2026-04-30 06:00:00 -0700",
         "value": "HKCategoryValueSleepAnalysisAsleepDeep"},
        {"type": "HKCategoryTypeIdentifierSleepAnalysis",
         "start": "2026-04-30 22:00:00 -0700", "end": "2026-05-01 06:00:00 -0700",
         "value": "HKCategoryValueSleepAnalysisAsleepREM"},
    ]
    xml = _build_records_xml(sleep_segs, workouts=[
        {"type": "HKWorkoutActivityTypeWalking",
         "start": "2026-05-01 12:00:00 -0700", "end": "2026-05-01 12:30:00 -0700",
         "duration": 30}
    ])
    fixture = tmp_path / "sleep.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    prs = personal_records(conn)

    longest_sleep = next((r for r in prs.records if r.metric == "Longest sleep night"), None)
    assert longest_sleep is not None
    assert longest_sleep.value == 8.0
    assert longest_sleep.unit == "h"
    assert longest_sleep.occurred_on == date(2026, 5, 1)


def test_personal_records_lowest_resting_hr_uses_min_not_max(tmp_path):
    from datetime import date
    from myhealth.analytics import personal_records

    conn = connect(tmp_path / "health.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    prs = personal_records(conn)

    rhr = next((r for r in prs.records if r.metric == "Lowest resting HR"), None)
    assert rhr is not None
    assert rhr.value == 55
    assert rhr.unit == "bpm"
    assert rhr.occurred_on == date(2026, 5, 1)
    assert rhr.higher_is_better is False


def test_personal_records_most_steps_sums_per_day(tmp_path):
    from datetime import date
    from myhealth.analytics import personal_records

    step_records = [
        {"type": "HKQuantityTypeIdentifierStepCount", "unit": "count",
         "start": "2026-04-28 07:00:00 -0700", "end": "2026-04-28 07:01:00 -0700", "value": 1000},
        {"type": "HKQuantityTypeIdentifierStepCount", "unit": "count",
         "start": "2026-04-28 10:00:00 -0700", "end": "2026-04-28 10:01:00 -0700", "value": 1000},
        {"type": "HKQuantityTypeIdentifierStepCount", "unit": "count",
         "start": "2026-04-28 15:00:00 -0700", "end": "2026-04-28 15:01:00 -0700", "value": 1000},
        {"type": "HKQuantityTypeIdentifierStepCount", "unit": "count",
         "start": "2026-04-29 09:00:00 -0700", "end": "2026-04-29 09:01:00 -0700", "value": 2500},
    ]
    xml = _build_records_xml(step_records)
    fixture = tmp_path / "steps.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    prs = personal_records(conn)

    steps_pr = next((r for r in prs.records if r.metric == "Most steps in a day"), None)
    assert steps_pr is not None
    assert steps_pr.value == 3000
    assert steps_pr.unit == "steps"
    assert steps_pr.occurred_on == date(2026, 4, 28)


def test_personal_records_handles_empty_db():
    from myhealth.analytics import personal_records

    conn = connect(":memory:")  # type: ignore[arg-type]
    prs = personal_records(conn)

    assert prs.records == []
    assert prs.current_streak_days == 0
    assert prs.longest_streak_days == 0
    assert prs.current_exercise_streak_days == 0


def test_personal_records_skips_metrics_not_in_db(tmp_path):
    from myhealth.analytics import personal_records

    conn = connect(tmp_path / "health.sqlite")
    insert_items(conn, parse_export(FIXTURE))

    prs = personal_records(conn)
    metrics = {r.metric for r in prs.records}

    # Not present in the fixture
    assert "Most steps in a day" not in metrics
    assert "Highest active energy day" not in metrics
    assert "Longest walk" not in metrics
    assert "Longest run" not in metrics
    # Only the five below should show up
    assert metrics <= {
        "Longest workout",
        "Biggest calorie burn (workout)",
        "Longest sleep night",
        "Highest HRV (SDNN)",
        "Lowest resting HR",
    }


def test_personal_records_current_streak_counts_consecutive_workout_days(tmp_path):
    from myhealth.analytics import personal_records

    workouts = [
        {"type": "HKWorkoutActivityTypeWalking",
         "start": "2026-04-28 08:00:00 -0700", "end": "2026-04-28 08:30:00 -0700",
         "duration": 30},
        {"type": "HKWorkoutActivityTypeWalking",
         "start": "2026-05-01 08:00:00 -0700", "end": "2026-05-01 08:30:00 -0700",
         "duration": 30},
        {"type": "HKWorkoutActivityTypeWalking",
         "start": "2026-05-02 08:00:00 -0700", "end": "2026-05-02 08:30:00 -0700",
         "duration": 30},
        {"type": "HKWorkoutActivityTypeWalking",
         "start": "2026-05-03 08:00:00 -0700", "end": "2026-05-03 08:30:00 -0700",
         "duration": 30},
    ]
    xml = _build_records_xml([], workouts=workouts)
    fixture = tmp_path / "streak.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    prs = personal_records(conn, 90)

    assert prs.current_streak_days == 3
    assert prs.longest_streak_days == 3


def test_personal_records_exercise_streak_requires_30_minutes(tmp_path):
    from myhealth.analytics import personal_records

    # Case 1: latest day sums to 45, prior day sums to 10 -> streak is 1
    xml = _build_records_xml([
        {"type": "HKQuantityTypeIdentifierAppleExerciseTime", "unit": "min",
         "start": "2026-04-30 09:00:00 -0700", "end": "2026-04-30 09:10:00 -0700", "value": 10},
        {"type": "HKQuantityTypeIdentifierAppleExerciseTime", "unit": "min",
         "start": "2026-05-01 09:00:00 -0700", "end": "2026-05-01 09:20:00 -0700", "value": 20},
        {"type": "HKQuantityTypeIdentifierAppleExerciseTime", "unit": "min",
         "start": "2026-05-01 17:00:00 -0700", "end": "2026-05-01 17:25:00 -0700", "value": 25},
    ])
    fixture = tmp_path / "exercise_short.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    prs = personal_records(conn, 90)
    assert prs.current_exercise_streak_days == 1

    # Case 2: two consecutive days both >= 30 min
    xml2 = _build_records_xml([
        {"type": "HKQuantityTypeIdentifierAppleExerciseTime", "unit": "min",
         "start": "2026-04-30 09:00:00 -0700", "end": "2026-04-30 09:30:00 -0700", "value": 30},
        {"type": "HKQuantityTypeIdentifierAppleExerciseTime", "unit": "min",
         "start": "2026-05-01 09:00:00 -0700", "end": "2026-05-01 09:40:00 -0700", "value": 40},
    ])
    fixture2 = tmp_path / "exercise_long.xml"
    fixture2.write_text(xml2)
    conn2 = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn2, parse_export(fixture2))

    prs2 = personal_records(conn2, 90)
    assert prs2.current_exercise_streak_days == 2

    # Case 3: metric absent from DB -> 0 (fixture has no ExerciseTime)
    conn3 = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn3, parse_export(FIXTURE))
    prs3 = personal_records(conn3, 90)
    assert prs3.current_exercise_streak_days == 0


def test_prs_command_prints_personal_records_and_streaks(tmp_path, capsys):
    from myhealth import cli as cli_mod

    db = tmp_path / "health.sqlite"
    conn = connect(db)
    insert_items(conn, parse_export(FIXTURE))

    cli_mod.prs(period="90d", db=db)
    output = capsys.readouterr().out

    assert "personal records" in output.lower()
    assert "Streaks" in output
    assert "Longest workout" in output
    assert "Lowest resting HR" in output


def test_html_report_includes_personal_records_section(tmp_path):
    from myhealth.reports import write_ai_html_report

    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)
    analysis = AIHealthAnalysis(
        overview="o", key_insights=["i"], recovery_assessment="r",
        workout_plan=[PlannedDay(day=1, recommendation="x", intensity="low", reason="r")],
        cautions=[],
    )

    path = write_ai_html_report(
        summary, analysis, PlanningStyle.balanced, "gpt-test", tmp_path, db_conn=conn
    )
    text = path.read_text()

    assert "Personal records" in text
    assert "Current workout streak" in text
    assert "Longest workout" in text
    assert "Longest streak" in text


def test_html_report_without_db_conn_still_renders(tmp_path):
    from myhealth.reports import write_ai_html_report

    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)
    analysis = AIHealthAnalysis(
        overview="o", key_insights=["i"], recovery_assessment="r",
        workout_plan=[PlannedDay(day=1, recommendation="x", intensity="low", reason="r")],
        cautions=[],
    )

    path = write_ai_html_report(
        summary, analysis, PlanningStyle.balanced, "gpt-test", tmp_path, db_conn=None
    )
    text = path.read_text()

    assert "Personal records" not in text


def test_personal_record_date_serializes_as_iso():
    from datetime import date
    from myhealth.models import PersonalRecord

    pr = PersonalRecord(metric="x", value=1.0, unit="mi", occurred_on=date(2026, 5, 1))
    dumped = pr.model_dump(mode="json")
    assert dumped["occurred_on"] == "2026-05-01"


# --- Habit streaks ---

def _build_sleep_xml(nights: list[tuple[str, float]], anchor_date: str = "2026-05-06") -> str:
    """Build a minimal Apple Health XML with sleep segments.

    nights: list of (wake_date_str, hours) where wake_date_str is e.g. '2026-05-06'
    Each night is encoded as a single Asleep segment ending at 07:00 on wake_date.
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE HealthData>',
        '<HealthData locale="en_US">',
    ]
    # Add an anchor workout so latest_datetime is deterministic
    parts.append(
        f'<Workout workoutActivityType="HKWorkoutActivityTypeWalking" duration="30" '
        f'durationUnit="min" sourceName="t" '
        f'startDate="{anchor_date} 08:00:00 -0700" '
        f'endDate="{anchor_date} 08:30:00 -0700" '
        f'totalEnergyBurned="100" totalEnergyBurnedUnit="kcal"/>'
    )
    for wake_date, hours in nights:
        # Start time = 07:00 minus hours on the same date (crosses midnight if >7h)
        from datetime import date as _date, timedelta
        wake_dt = _date.fromisoformat(wake_date)
        # encode as a single segment: starts at (wake_date 07:00 - hours)
        # for simplicity encode as start on wake_date 00:00, end at hours later
        start_str = f"{wake_date} 00:00:00 -0700"
        # end = start + hours
        from datetime import datetime as _dt
        start = _dt(wake_dt.year, wake_dt.month, wake_dt.day, 0, 0, 0)
        end = start + timedelta(hours=hours)
        end_str = end.strftime("%Y-%m-%d %H:%M:%S") + " -0700"
        parts.append(
            f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="t" unit="" '
            f'creationDate="{start_str}" startDate="{start_str}" endDate="{end_str}" '
            f'value="HKCategoryValueSleepAnalysisAsleepCore"/>'
        )
    parts.append("</HealthData>")
    return "\n".join(parts)


def _build_rhr_xml(readings: list[tuple[str, float]]) -> str:
    """Build a minimal XML with RHR records.
    readings: list of (date_str, value)
    Includes a workout on the last date as anchor.
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE HealthData>',
        '<HealthData locale="en_US">',
    ]
    for d, v in readings:
        parts.append(
            f'<Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="t" '
            f'unit="count/min" creationDate="{d} 08:00:00 -0700" '
            f'startDate="{d} 08:00:00 -0700" endDate="{d} 08:00:00 -0700" value="{v}"/>'
        )
    if readings:
        last_date = readings[-1][0]
        parts.append(
            f'<Workout workoutActivityType="HKWorkoutActivityTypeWalking" duration="30" '
            f'durationUnit="min" sourceName="t" '
            f'startDate="{last_date} 10:00:00 -0700" '
            f'endDate="{last_date} 10:30:00 -0700" '
            f'totalEnergyBurned="100" totalEnergyBurnedUnit="kcal"/>'
        )
    parts.append("</HealthData>")
    return "\n".join(parts)


def _build_steps_xml(readings: list[tuple[str, float]]) -> str:
    """Build a minimal XML with step records. readings: list of (date_str, value)"""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE HealthData>',
        '<HealthData locale="en_US">',
    ]
    for d, v in readings:
        parts.append(
            f'<Record type="HKQuantityTypeIdentifierStepCount" sourceName="t" '
            f'unit="count" creationDate="{d} 09:00:00 -0700" '
            f'startDate="{d} 09:00:00 -0700" endDate="{d} 09:01:00 -0700" value="{v}"/>'
        )
    if readings:
        last_date = readings[-1][0]
        parts.append(
            f'<Workout workoutActivityType="HKWorkoutActivityTypeWalking" duration="30" '
            f'durationUnit="min" sourceName="t" '
            f'startDate="{last_date} 10:00:00 -0700" '
            f'endDate="{last_date} 10:30:00 -0700" '
            f'totalEnergyBurned="100" totalEnergyBurnedUnit="kcal"/>'
        )
    parts.append("</HealthData>")
    return "\n".join(parts)


def test_compute_streaks_empty_db_returns_empty():
    """Empty DB: anchor_date is None and streaks is an empty list."""
    from myhealth.analytics import compute_streaks

    conn = connect(":memory:")  # type: ignore[arg-type]
    result = compute_streaks(conn, period_days=30)

    assert result.anchor_date is None
    assert result.streaks == []


def test_compute_streaks_three_consecutive_sleep_days(tmp_path):
    """3 nights of 7.5 h sleep -> current_streak==3, longest_streak==3, active_today==True."""
    from myhealth.analytics import compute_streaks

    nights = [
        ("2026-05-04", 7.5),
        ("2026-05-05", 7.5),
        ("2026-05-06", 7.5),
    ]
    xml = _build_sleep_xml(nights, anchor_date="2026-05-06")
    fixture = tmp_path / "sleep3.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    result = compute_streaks(conn, period_days=30)

    sleep_streak = next(s for s in result.streaks if s.metric == "sleep_hours")
    assert sleep_streak.current_streak == 3
    assert sleep_streak.longest_streak == 3
    assert sleep_streak.active_today is True


def test_compute_streaks_broken_streak_resets_current(tmp_path):
    """Pattern [7.5, 6.0, 7.5, 7.5, 7.5]: current_streak==3, longest_streak==3."""
    from myhealth.analytics import compute_streaks

    nights = [
        ("2026-05-02", 7.5),
        ("2026-05-03", 6.0),  # breaks default >= 7.0
        ("2026-05-04", 7.5),
        ("2026-05-05", 7.5),
        ("2026-05-06", 7.5),
    ]
    xml = _build_sleep_xml(nights, anchor_date="2026-05-06")
    fixture = tmp_path / "sleep_broken.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    result = compute_streaks(conn, period_days=30)

    sleep_streak = next(s for s in result.streaks if s.metric == "sleep_hours")
    assert sleep_streak.current_streak == 3
    assert sleep_streak.longest_streak == 3


def test_compute_streaks_rhr_uses_lower_is_better(tmp_path):
    """RHR [58, 55, 72] with default <=60: current_streak==0, longest_streak==2."""
    from myhealth.analytics import compute_streaks

    readings = [
        ("2026-05-04", 58.0),
        ("2026-05-05", 55.0),
        ("2026-05-06", 72.0),
    ]
    xml = _build_rhr_xml(readings)
    fixture = tmp_path / "rhr.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    result = compute_streaks(conn, period_days=30)

    rhr_streak = next(s for s in result.streaks if s.metric == "rhr")
    assert rhr_streak.current_streak == 0
    assert rhr_streak.longest_streak == 2


def test_compute_streaks_steps_sum_per_day(tmp_path):
    """Two step records on anchor day totalling 9000 + prior day 8500: current_streak==2."""
    from myhealth.analytics import compute_streaks

    readings = [
        ("2026-05-05", 8500.0),  # prior day
        ("2026-05-06", 5000.0),  # anchor day record 1
        ("2026-05-06", 4000.0),  # anchor day record 2 -> total 9000
    ]
    xml = _build_steps_xml(readings)
    fixture = tmp_path / "steps2.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    result = compute_streaks(conn, period_days=30)

    steps_streak = next(s for s in result.streaks if s.metric == "steps")
    assert steps_streak.current_streak == 2


def test_compute_streaks_missing_day_breaks_streak(tmp_path):
    """Days 1,2 have 7.5h sleep; anchor has no sleep -> current==0, longest==2, active_today==False."""
    from myhealth.analytics import compute_streaks

    nights = [
        ("2026-05-04", 7.5),
        ("2026-05-05", 7.5),
        # 2026-05-06 (anchor) has no sleep record
    ]
    xml = _build_sleep_xml(nights, anchor_date="2026-05-06")
    fixture = tmp_path / "sleep_missing.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    result = compute_streaks(conn, period_days=30)

    sleep_streak = next(s for s in result.streaks if s.metric == "sleep_hours")
    assert sleep_streak.current_streak == 0
    assert sleep_streak.longest_streak == 2
    assert sleep_streak.active_today is False


def test_compute_streaks_override_raises_threshold(tmp_path):
    """3 days 7.5h sleep with threshold overridden to 8.0: current_streak==0."""
    from myhealth.analytics import compute_streaks

    nights = [
        ("2026-05-04", 7.5),
        ("2026-05-05", 7.5),
        ("2026-05-06", 7.5),
    ]
    xml = _build_sleep_xml(nights, anchor_date="2026-05-06")
    fixture = tmp_path / "sleep_override.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    result = compute_streaks(conn, period_days=30, thresholds={"sleep_hours": 8.0})

    sleep_streak = next(s for s in result.streaks if s.metric == "sleep_hours")
    assert sleep_streak.current_streak == 0


def test_compute_streaks_unknown_threshold_key_ignored(tmp_path):
    """Unknown key in thresholds dict is silently ignored; no 'bogus' metric in streaks."""
    from myhealth.analytics import compute_streaks

    nights = [("2026-05-06", 7.5)]
    xml = _build_sleep_xml(nights, anchor_date="2026-05-06")
    fixture = tmp_path / "sleep_bogus.xml"
    fixture.write_text(xml)
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(fixture))

    result = compute_streaks(conn, period_days=30, thresholds={"bogus": 99.0})

    metric_names = [s.metric for s in result.streaks]
    assert "bogus" not in metric_names
    # Should still return normally with the five standard metrics
    assert len(result.streaks) == 5


def test_compute_streaks_returns_all_five_metrics_in_order():
    """Streaks list always has exactly five metrics in canonical order."""
    from myhealth.analytics import compute_streaks

    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))

    result = compute_streaks(conn, period_days=30)

    assert [s.metric for s in result.streaks] == [
        "sleep_hours", "steps", "hrv_ms", "active_calories", "rhr"
    ]


def test_streaks_cli_runs_offline(tmp_path):
    """myhealth streaks --db ... --period 30d exits 0 and prints Sleep and Steps."""
    from typer.testing import CliRunner
    from myhealth.cli import app

    db = tmp_path / "health.sqlite"
    conn = connect(db)
    insert_items(conn, parse_export(FIXTURE))
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["streaks", "--db", str(db), "--period", "30d"])

    assert result.exit_code == 0, f"Non-zero exit: {result.output}"
    assert "Sleep" in result.output
    assert "Steps" in result.output


def test_streaks_cli_goal_override_parses(tmp_path):
    """--goal sleep=8 --goal steps=10000 are parsed and passed through without error."""
    from typer.testing import CliRunner
    from myhealth.cli import app

    db = tmp_path / "health.sqlite"
    conn = connect(db)
    insert_items(conn, parse_export(FIXTURE))
    conn.close()

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["streaks", "--goal", "sleep=8", "--goal", "steps=10000", "--db", str(db)],
    )

    assert result.exit_code == 0, f"Non-zero exit: {result.output}"


def test_streaks_cli_bad_goal_reports_error(tmp_path):
    """--goal sleep (missing '=') should produce a non-zero exit code."""
    from typer.testing import CliRunner
    from myhealth.cli import app

    db = tmp_path / "health.sqlite"

    runner = CliRunner()
    result = runner.invoke(app, ["streaks", "--goal", "sleep", "--db", str(db)])

    assert result.exit_code != 0
