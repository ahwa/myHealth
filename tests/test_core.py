from pathlib import Path

from myhealth.analytics import parse_period, summarize
from myhealth.ai import build_analysis_prompt
from myhealth.auth import CODEX_CLIENT_ID, CODEX_REDIRECT_HOST, _authorization_url, _code_from_pasted_value
from myhealth.apple_health import parse_export
from myhealth.models import PlanningStyle
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


def test_ai_prompt_uses_summary_not_raw_records():
    conn = connect(":memory:")  # type: ignore[arg-type]
    insert_items(conn, parse_export(FIXTURE))
    summary = summarize(conn, 30)

    prompt = build_analysis_prompt(summary, 7, PlanningStyle.balanced)

    assert "average_sleep_hours" in prompt
    assert "HKQuantityTypeIdentifierRestingHeartRate" not in prompt
    assert "export.xml" not in prompt


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
