"""Load and validate the pipeline universe and date ranges from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when the config file is missing required keys or is malformed."""


@dataclass(frozen=True, slots=True)
class EquitiesConfig:
    tickers: list[str]
    start: date
    end: date | None


@dataclass(frozen=True, slots=True)
class MacroConfig:
    series: list[str]
    start: date
    end: date | None


@dataclass(frozen=True, slots=True)
class Config:
    equities: EquitiesConfig
    macro: MacroConfig


def load_config(path: Path | str) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    return Config(
        equities=_parse_equities(_require_mapping(raw, "equities")),
        macro=_parse_macro(_require_mapping(raw, "macro")),
    )


def _parse_equities(raw: dict[str, Any]) -> EquitiesConfig:
    return EquitiesConfig(
        tickers=_require_str_list(raw, "tickers", section="equities"),
        start=_require_date(raw, "start", section="equities"),
        end=_optional_date(raw, "end", section="equities"),
    )


def _parse_macro(raw: dict[str, Any]) -> MacroConfig:
    return MacroConfig(
        series=_require_str_list(raw, "series", section="macro"),
        start=_require_date(raw, "start", section="macro"),
        end=_optional_date(raw, "end", section="macro"),
    )


def _require_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"missing or invalid '{key}' section")
    return value


def _require_str_list(raw: dict[str, Any], key: str, *, section: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"'{section}.{key}' must be a non-empty list")
    if not all(isinstance(item, str) for item in value):
        raise ConfigError(f"'{section}.{key}' must contain only strings")
    return list(value)


def _require_date(raw: dict[str, Any], key: str, *, section: str) -> date:
    value = raw.get(key)
    if value is None:
        raise ConfigError(f"'{section}.{key}' is required")
    return _coerce_date(value, where=f"{section}.{key}")


def _optional_date(raw: dict[str, Any], key: str, *, section: str) -> date | None:
    value = raw.get(key)
    if value is None:
        return None
    return _coerce_date(value, where=f"{section}.{key}")


def _coerce_date(value: Any, *, where: str) -> date:
    # PyYAML already parses unquoted ISO dates into date objects; quoted ones
    # arrive as strings. Accept both so the YAML can be written either way.
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"'{where}' is not a valid ISO date: {value!r}") from exc
    raise ConfigError(f"'{where}' must be an ISO date string, got {type(value).__name__}")
