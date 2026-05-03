from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_DB_PATH
from .models import HealthRecord, Workout


SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,
  source TEXT,
  unit TEXT,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  value REAL,
  value_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_type_start ON records(type, start_date);

CREATE TABLE IF NOT EXISTS workouts (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,
  source TEXT,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  duration_minutes REAL NOT NULL,
  total_energy_kcal REAL,
  distance_km REAL
);
CREATE INDEX IF NOT EXISTS idx_workouts_type_start ON workouts(type, start_date);
"""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def clear(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM records")
    conn.execute("DELETE FROM workouts")
    conn.commit()


def insert_items(conn: sqlite3.Connection, items: Iterable[HealthRecord | Workout]) -> dict[str, int]:
    counts = {"records": 0, "workouts": 0}
    for item in items:
        if isinstance(item, Workout):
            conn.execute(
                """
                INSERT INTO workouts
                  (type, source, start_date, end_date, duration_minutes, total_energy_kcal, distance_km)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.type,
                    item.source,
                    item.start_date.isoformat(),
                    item.end_date.isoformat(),
                    item.duration_minutes,
                    item.total_energy_kcal,
                    item.distance_km,
                ),
            )
            counts["workouts"] += 1
        else:
            conn.execute(
                """
                INSERT INTO records
                  (type, source, unit, start_date, end_date, value, value_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.type,
                    item.source,
                    item.unit,
                    item.start_date.isoformat(),
                    item.end_date.isoformat(),
                    item.value,
                    item.value_text,
                ),
            )
            counts["records"] += 1
    conn.commit()
    return counts


def latest_datetime(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        """
        SELECT MAX(max_date) AS latest FROM (
          SELECT MAX(end_date) AS max_date FROM records
          UNION ALL
          SELECT MAX(end_date) AS max_date FROM workouts
        )
        """
    ).fetchone()
    if not row or not row["latest"]:
        return None
    return datetime.fromisoformat(row["latest"])
