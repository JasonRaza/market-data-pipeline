from datetime import date
from pathlib import Path

import pandas as pd

from mdp.ingest.equities import TIDY_COLUMNS as EQ_COLS
from mdp.storage.parquet import (
    read_equities,
    read_macro,
    write_equities,
    write_macro,
)


def _equities(ticker: str = "AAPL") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": [ticker, ticker],
            "date": [date(2023, 1, 3), date(2023, 1, 4)],
            "open": [125.0, 126.0],
            "high": [128.0, 127.5],
            "low": [124.0, 125.5],
            "close": [127.0, 126.5],
            "adj_close": [126.1, 125.6],
            "volume": [1_000_000, 900_000],
            "dividends": [0.0, 0.23],
            "stock_splits": [0.0, 0.0],
        }
    )


def _macro() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": ["DGS10", "DGS10"],
            "date": [date(2023, 1, 2), date(2023, 1, 3)],
            "value": [3.79, 3.71],
        }
    )


def test_equities_round_trip(tmp_path: Path) -> None:
    write_equities(_equities(), tmp_path)
    out = read_equities(tmp_path)

    assert list(out.columns) == EQ_COLS
    assert len(out) == 2
    # date survives as python date, not a tz-aware timestamp.
    assert isinstance(out.loc[0, "date"], date)
    assert out.loc[0, "adj_close"] == 126.1


def test_partition_directory_layout(tmp_path: Path) -> None:
    write_equities(_equities("MSFT"), tmp_path)
    assert (tmp_path / "equities" / "ticker=MSFT").is_dir()


def test_rewrite_is_idempotent(tmp_path: Path) -> None:
    write_equities(_equities(), tmp_path)
    write_equities(_equities(), tmp_path)  # same partition, written twice

    out = read_equities(tmp_path)
    assert len(out) == 2  # not duplicated


def test_writing_new_ticker_keeps_existing(tmp_path: Path) -> None:
    write_equities(_equities("AAPL"), tmp_path)
    write_equities(_equities("MSFT"), tmp_path)

    out = read_equities(tmp_path)
    assert set(out["ticker"]) == {"AAPL", "MSFT"}
    assert len(out) == 4


def test_macro_round_trip(tmp_path: Path) -> None:
    write_macro(_macro(), tmp_path)
    out = read_macro(tmp_path)

    assert set(out["series_id"]) == {"DGS10"}
    assert len(out) == 2


def test_read_missing_returns_empty(tmp_path: Path) -> None:
    out = read_equities(tmp_path)
    assert out.empty
    assert list(out.columns) == EQ_COLS
