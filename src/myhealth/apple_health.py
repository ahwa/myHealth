from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree.ElementTree import iterparse
from zipfile import ZipFile, is_zipfile

from .models import HealthRecord, Workout


def parse_apple_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")


@contextmanager
def export_xml_path(path: Path) -> Iterator[Path]:
    if path.suffix.lower() == ".xml":
        yield path
        return
    if not is_zipfile(path):
        raise ValueError("Expected an Apple Health export .zip or export.xml file.")
    with TemporaryDirectory() as tmpdir:
        with ZipFile(path) as archive:
            candidates = [name for name in archive.namelist() if name.endswith("export.xml")]
            if not candidates:
                raise ValueError("Could not find export.xml in the Apple Health ZIP.")
            extracted = Path(archive.extract(candidates[0], tmpdir))
            yield extracted


def _float_or_none(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _duration_minutes(value: str | None, unit: str | None) -> float:
    duration = _float_or_none(value) or 0.0
    normalized = (unit or "min").lower()
    if normalized in {"sec", "s", "second", "seconds"}:
        return duration / 60
    if normalized in {"hr", "h", "hour", "hours"}:
        return duration * 60
    return duration


def _distance_km(value: str | None, unit: str | None) -> float | None:
    distance = _float_or_none(value)
    if distance is None:
        return None
    normalized = (unit or "").lower()
    if normalized in {"mi", "mile", "miles"}:
        return distance * 1.609344
    if normalized in {"m", "meter", "meters"}:
        return distance / 1000
    return distance


def parse_export(path: Path) -> Iterator[HealthRecord | Workout]:
    with export_xml_path(path) as xml_path:
        for _, elem in iterparse(xml_path, events=("end",)):
            if elem.tag == "Record":
                attrs = elem.attrib
                raw_value = attrs.get("value")
                yield HealthRecord(
                    type=attrs["type"],
                    source=attrs.get("sourceName"),
                    unit=attrs.get("unit"),
                    start_date=parse_apple_datetime(attrs["startDate"]),
                    end_date=parse_apple_datetime(attrs["endDate"]),
                    value=_float_or_none(raw_value),
                    value_text=raw_value if _float_or_none(raw_value) is None else None,
                )
                elem.clear()
            elif elem.tag == "Workout":
                attrs = elem.attrib
                yield Workout(
                    type=attrs["workoutActivityType"],
                    source=attrs.get("sourceName"),
                    start_date=parse_apple_datetime(attrs["startDate"]),
                    end_date=parse_apple_datetime(attrs["endDate"]),
                    duration_minutes=_duration_minutes(
                        attrs.get("duration"), attrs.get("durationUnit")
                    ),
                    total_energy_kcal=_float_or_none(attrs.get("totalEnergyBurned")),
                    distance_km=_distance_km(
                        attrs.get("totalDistance"), attrs.get("totalDistanceUnit")
                    ),
                )
                elem.clear()
