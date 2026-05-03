from __future__ import annotations

from pathlib import Path
from typing import Any

import tomllib

from .models import PlanningStyle


APP_DIR = Path.home() / "Library" / "Application Support" / "myhealth"
DEFAULT_DB_PATH = APP_DIR / "myhealth.sqlite"
DEFAULT_CONFIG_PATH = APP_DIR / "config.toml"


DEFAULT_CONFIG: dict[str, Any] = {
    "planning_style": PlanningStyle.balanced.value,
    "report_dir": "reports",
}


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return DEFAULT_CONFIG.copy()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    config = DEFAULT_CONFIG.copy()
    config.update(data)
    return config


def save_config(config: dict[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in config.items():
        if isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_config_value(key: str, value: str, path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config = load_config(path)
    if key == "planning_style":
        PlanningStyle(value)
    elif key != "report_dir":
        raise ValueError(f"Unsupported config key: {key}")
    config[key] = value
    save_config(config, path)
    return config

