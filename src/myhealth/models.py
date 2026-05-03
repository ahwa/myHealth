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
    resting_hr_avg: Optional[float]
    hrv_ms_avg: Optional[float]
    strength_sessions: int
    workout_sessions: int
    active_days: int
    recovery_score: int
    recovery_status: str
    notes: list[str]


class PlannedDay(BaseModel):
    day: int
    recommendation: str
    intensity: str
    reason: str

