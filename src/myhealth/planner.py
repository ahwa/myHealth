from __future__ import annotations

from .models import MetricSummary, PlannedDay, PlanningStyle


def make_plan(summary: MetricSummary, days: int, style: PlanningStyle) -> list[PlannedDay]:
    if days <= 0:
        raise ValueError("Plan length must be positive.")

    recovery_threshold = {
        PlanningStyle.conservative: 70,
        PlanningStyle.balanced: 55,
        PlanningStyle.aggressive: 40,
    }[style]
    heavy_threshold = {
        PlanningStyle.conservative: 82,
        PlanningStyle.balanced: 72,
        PlanningStyle.aggressive: 60,
    }[style]

    plan: list[PlannedDay] = []
    heavy_count = 0
    strength_count = 0
    for day in range(1, days + 1):
        score = summary.recovery_score
        if score < recovery_threshold:
            recommendation = "Mobility and recovery"
            intensity = "low"
            reason = f"{style.value} mode still protects recovery when readiness is {score}/100."
        elif score >= heavy_threshold and heavy_count < (3 if style == PlanningStyle.aggressive else 2):
            recommendation = "Heavy strength"
            intensity = "high"
            reason = "Readiness supports a hard strength session."
            heavy_count += 1
            strength_count += 1
        elif strength_count < (5 if style == PlanningStyle.aggressive else 4 if style == PlanningStyle.balanced else 3):
            recommendation = "Moderate strength"
            intensity = "moderate"
            reason = "Keep strength frequency progressing while managing fatigue."
            strength_count += 1
        else:
            recommendation = "Recovery or easy cardio"
            intensity = "low"
            reason = "Enough strength work is planned for this window."

        if day > 1 and plan[-1].recommendation == "Heavy strength" and recommendation == "Heavy strength":
            recommendation = "Moderate strength"
            intensity = "moderate"
            reason = "Avoid stacking heavy strength days back to back."
            heavy_count -= 1

        plan.append(
            PlannedDay(
                day=day,
                recommendation=recommendation,
                intensity=intensity,
                reason=reason,
            )
        )
    return plan

