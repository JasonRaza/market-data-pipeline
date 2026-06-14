from datetime import date
from pathlib import Path

import pytest

from mdp.config import ConfigError, load_config

VALID = """
equities:
  tickers: [AAPL, MSFT]
  start: "2020-01-01"
  end: "2023-12-31"
macro:
  series: [DGS10]
  start: "2020-01-01"
  end: null
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tickers.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_parses_sections(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID))

    assert cfg.equities.tickers == ["AAPL", "MSFT"]
    assert cfg.equities.start == date(2020, 1, 1)
    assert cfg.equities.end == date(2023, 12, 31)
    assert cfg.macro.series == ["DGS10"]
    assert cfg.macro.end is None


def test_load_config_accepts_unquoted_yaml_dates(tmp_path: Path) -> None:
    text = VALID.replace('"2020-01-01"', "2020-01-01")
    cfg = load_config(_write(tmp_path, text))

    assert cfg.equities.start == date(2020, 1, 1)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_empty_ticker_list_rejected(tmp_path: Path) -> None:
    text = VALID.replace("[AAPL, MSFT]", "[]")
    with pytest.raises(ConfigError, match="non-empty list"):
        load_config(_write(tmp_path, text))


def test_bad_date_rejected(tmp_path: Path) -> None:
    text = VALID.replace('"2020-01-01"', '"not-a-date"')
    with pytest.raises(ConfigError, match="valid ISO date"):
        load_config(_write(tmp_path, text))
