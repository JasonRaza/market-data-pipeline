from datetime import date

import pandas as pd
import pytest

from mdp.ingest.equities import (
    TIDY_COLUMNS,
    IngestError,
    fetch_equities,
    fetch_many_equities,
    tidy_equities,
)


def _raw_yfinance_frame() -> pd.DataFrame:
    """A frame shaped like ``yf.Ticker(...).history(auto_adjust=False)``."""
    index = pd.DatetimeIndex(["2023-01-03", "2023-01-04"], name="Date")
    return pd.DataFrame(
        {
            "Open": [125.0, 126.0],
            "High": [128.0, 127.5],
            "Low": [124.0, 125.5],
            "Close": [127.0, 126.5],
            "Adj Close": [126.1, 125.6],
            "Volume": [1000000, 900000],
            "Dividends": [0.0, 0.23],
            "Stock Splits": [0.0, 0.0],
        },
        index=index,
    )


def test_tidy_reshapes_to_long_schema() -> None:
    out = tidy_equities(_raw_yfinance_frame(), "AAPL")

    assert list(out.columns) == TIDY_COLUMNS
    assert len(out) == 2
    assert (out["ticker"] == "AAPL").all()
    assert out.loc[0, "date"] == date(2023, 1, 3)


def test_tidy_preserves_adjusted_close_and_actions() -> None:
    out = tidy_equities(_raw_yfinance_frame(), "AAPL")

    # adj_close must stay distinct from the unadjusted close, and the dividend
    # event must survive the reshape.
    assert out.loc[0, "close"] == 127.0
    assert out.loc[0, "adj_close"] == 126.1
    assert out.loc[1, "dividends"] == pytest.approx(0.23)


def test_tidy_drops_rows_with_null_prices() -> None:
    raw = _raw_yfinance_frame()
    raw.loc[raw.index[0], "Close"] = None

    out = tidy_equities(raw, "AAPL")

    assert len(out) == 1
    assert out.loc[0, "date"] == date(2023, 1, 4)


def test_tidy_fills_missing_action_columns() -> None:
    raw = _raw_yfinance_frame().drop(columns=["Dividends", "Stock Splits"])

    out = tidy_equities(raw, "AAPL")

    assert (out["dividends"] == 0.0).all()
    assert (out["stock_splits"] == 0.0).all()


def test_tidy_raises_on_missing_price_columns() -> None:
    raw = _raw_yfinance_frame().drop(columns=["Adj Close"])

    with pytest.raises(IngestError, match="adj_close"):
        tidy_equities(raw, "AAPL")


def test_empty_frame_returns_typed_empty() -> None:
    out = tidy_equities(pd.DataFrame(), "AAPL")

    assert list(out.columns) == TIDY_COLUMNS
    assert out.empty


def test_fetch_uses_injected_fetcher() -> None:
    captured: dict[str, object] = {}

    def fake(ticker: str, start: date, end: date | None) -> pd.DataFrame:
        captured["args"] = (ticker, start, end)
        return _raw_yfinance_frame()

    out = fetch_equities("MSFT", date(2023, 1, 1), None, fetcher=fake)

    assert captured["args"] == ("MSFT", date(2023, 1, 1), None)
    assert (out["ticker"] == "MSFT").all()


def test_fetch_many_concatenates() -> None:
    def fake(ticker: str, start: date, end: date | None) -> pd.DataFrame:
        return _raw_yfinance_frame()

    out = fetch_many_equities(["AAPL", "MSFT"], date(2023, 1, 1), None, fetcher=fake)

    assert set(out["ticker"]) == {"AAPL", "MSFT"}
    assert len(out) == 4
