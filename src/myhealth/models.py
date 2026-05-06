from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class PlanningStyle(str, Enum):
    conservative = "conservative"
    balanced = "balanced"
    aggressive = "aggressive"


class HealthRecord(BaseModel):
    type: str
    source: Optional[str] = None
    unit: Optional[str] = None
    start_date: datetime
    end_date: datetime
    value: Optional[float] = None
    value_text: Optional[str] = None


class Workout(BaseModel):
    type: str
    source: Optional[str] = None
    start_date: datetime
    end_date: datetime
    duration_minutes: float
    total_energy_kcal: Optional[float] = None
    distance_km: Optional[float] = None


class MetricSummary(BaseModel):
    period_days: int
    sleep_hours_avg: Optional[float]
    sleep_hours_stdev: Optional[float] = None
    resting_hr_avg: Optional[float]
    resting_hr_stdev: Optional[float] = None
    hrv_ms_avg: Optional[float]
    hrv_ms_stdev: Optional[float] = None
    strength_sessions: int
    workout_sessions: int
    active_days: int
    weekly_strength_frequency: Optional[float] = None
    total_active_hours: Optional[float] = None
    workouts_by_type: dict[str, int] = {}
    data_completeness: dict[str, int] = {}
    trend_changes: dict[str, float] = {}
    recovery_score: int
    recovery_status: str
    notes: list[str]


class PlannedDay(BaseModel):
    day: int
    recommendation: str
    intensity: str
    reason: str


class AIHealthAnalysis(BaseModel):
    overview: str
    key_insights: list[str]
    recovery_assessment: str
    workout_plan: list[PlannedDay]
    cautions: list[str]
