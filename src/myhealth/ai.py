from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from openai import OpenAI

from .auth import get_valid_codex_token
from .models import AIHealthAnalysis, MetricSummary, PlanningStyle


DEFAULT_MODEL = "gpt-5.2"
# When authed with a ChatGPT account (not an API key) the Codex backend
# only accepts a tight allowlist of models that depends on the plan tier.
# As of May 2026 the broadly-available choice for ChatGPT Plus is
# 'gpt-5.4-mini' (cheap, fast). Pro tier users may prefer 'gpt-5.5'.
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
# Fallbacks tried in order when the backend reports "model not supported".
CODEX_MODEL_FALLBACKS = (
    "gpt-5.4-mini",
    "gpt-5.3-codex-spark",
    "gpt-5.5",
    "gpt-5.1-codex-max",
    "gpt-4.1",
)
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overview": {"type": "string"},
            "key_insights": {"type": "array", "items": {"type": "string"}},
            "recovery_assessment": {"type": "string"},
            "workout_plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "day": {"type": "integer"},
                        "recommendation": {"type": "string"},
                        "intensity": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["day", "recommendation", "intensity", "reason"],
                },
            },
            "cautions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "overview",
            "key_insights",
            "recovery_assessment",
            "workout_plan",
            "cautions",
        ],
    }


def _metric_payload(summary: MetricSummary, days: int, style: PlanningStyle) -> dict[str, Any]:
    return {
        "analysis_period_days": summary.period_days,
        "requested_plan_days": days,
        "planning_style": style.value,
        "training_focus": "strength training",
        "metrics": {
            "average_sleep_hours": summary.sleep_hours_avg,
            "sleep_hours_stdev": summary.sleep_hours_stdev,
            "average_resting_heart_rate_bpm": summary.resting_hr_avg,
            "resting_hr_stdev_bpm": summary.resting_hr_stdev,
            "average_hrv_sdnn_ms": summary.hrv_ms_avg,
            "hrv_sdnn_stdev_ms": summary.hrv_ms_stdev,
            "strength_sessions": summary.strength_sessions,
            "workout_sessions": summary.workout_sessions,
            "active_workout_days": summary.active_days,
            "weekly_strength_frequency": summary.weekly_strength_frequency,
            "total_active_hours": summary.total_active_hours,
        },
        "workouts_by_type": summary.workouts_by_type,
        "data_completeness": summary.data_completeness,
        "trend_changes": summary.trend_changes,
        "summary_notes": list(summary.notes),
    }


def build_analysis_prompt(summary: MetricSummary, days: int, style: PlanningStyle) -> str:
    payload = _metric_payload(summary, days, style)
    return (
        "Analyze this Apple Health workout and recovery summary, then produce JSON "
        "matching the requested schema. Create strength-training-oriented insights "
        "and a day-by-day workout/recovery plan. Honor the planning style: "
        "conservative means bias toward recovery, balanced means preserve training "
        "continuity with prudent recovery, aggressive means prefer progression unless "
        "the data strongly suggests recovery. Do not diagnose medical conditions. "
        "Use only the provided data and call out missing data when it matters.\n\n"
        f"DATA:\n{json.dumps(payload, indent=2)}"
    )


def analyze_with_ai(
    summary: MetricSummary,
    days: int,
    style: PlanningStyle,
    model: str = DEFAULT_MODEL,
) -> AIHealthAnalysis:
    client = OpenAI()
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a careful strength and recovery coach. Provide practical, "
                    "evidence-informed fitness guidance from summarized personal health "
                    "data. This is not medical advice."
                ),
            },
            {"role": "user", "content": build_analysis_prompt(summary, days, style)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "health_analysis",
                "strict": True,
                "schema": _schema(),
            }
        },
    )
    return AIHealthAnalysis.parse_raw(response.output_text)


def _extract_completed_text(event: dict[str, Any]) -> str:
    response = event.get("response") or {}
    chunks: list[str] = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    return "".join(chunks)


def analyze_with_codex_oauth(
    summary: MetricSummary,
    days: int,
    style: PlanningStyle,
    model: str = DEFAULT_CODEX_MODEL,
) -> AIHealthAnalysis:
    token = get_valid_codex_token()
    body = {
        "model": model,
        "instructions": (
            "You are a careful strength and recovery coach. Provide practical, "
            "evidence-informed fitness guidance from summarized personal health "
            "data. This is not medical advice."
        ),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": build_analysis_prompt(summary, days, style)}
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "health_analysis",
                "strict": True,
                "schema": _schema(),
            }
        },
        "stream": True,
        "store": False,
    }
    headers = {
        "Authorization": f"Bearer {token.access}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "myhealth/0.1.0",
        "OpenAI-Beta": "responses=v1",
    }
    if token.account_id:
        headers["chatgpt-account-id"] = token.account_id
    request = urllib.request.Request(
        CODEX_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    text_parts: list[str] = []
    completed_text = ""
    try:
        response_ctx = urllib.request.urlopen(request, timeout=180)
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        snippet = err_body[:1000].strip()
        # Detect "model not supported for ChatGPT account" 400s and add a hint.
        hint = ""
        if exc.code == 400 and "not supported" in snippet and "model" in snippet.lower():
            others = [m for m in CODEX_MODEL_FALLBACKS if m != model]
            hint = (
                f"\nThis usually means '{model}' is gated by your ChatGPT plan tier. "
                f"Try one of: {', '.join(others)} via "
                f"`myhealth plan --provider openai-codex --model <name>`."
            )
        raise RuntimeError(
            f"Codex OAuth request failed with HTTP {exc.code} {exc.reason}. "
            f"Response body: {snippet or '(empty)'}{hint}"
        ) from exc
    with response_ctx as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            event = json.loads(data)
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                text_parts.append(event.get("delta", ""))
            elif event_type == "response.completed":
                completed_text = _extract_completed_text(event)
    output = completed_text or "".join(text_parts)
    if not output:
        raise RuntimeError("Codex OAuth response completed without output text.")
    return AIHealthAnalysis.parse_raw(output)
