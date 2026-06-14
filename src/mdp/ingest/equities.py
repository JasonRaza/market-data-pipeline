"""Fetch equity OHLCV bars from yfinance and reshape into a tidy frame.

The tidy schema is one row per (ticker, date) with these columns:

    ticker, date, open, high, low, close, adj_close, volume,
    dividends, stock_splits

We deliberately keep BOTH the unadjusted ``close`` and the back-adjusted
``adj_close``, plus the raw ``dividends`` and ``stock_splits`` events, so that
adjustment can be recomputed or audited later rather than baked in irreversibly.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

import pandas as pd

from mdp.ingest import IngestError

TIDY_COLUMNS: list[str] = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "dividends",
    "stock_splits",
]

_PRICE_COLUMNS = ["open", "high", "low", "close", "adj_close"]

# yfinance -> tidy column names.
_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Dividends": "dividends",
    "Stock Splits": "stock_splits",
}

# Signature of the unit doing the network call. Injected so tests can supply a
# canned yfinance-shaped frame without touching the network.
EquityFetcher = Callable[[str, date, date | None], pd.DataFrame]


def fetch_equities(
    ticker: str,
    start: date,
    end: date | None,
    *,
    fetcher: EquityFetcher | None = None,
) -> pd.DataFrame:
    fetch = fetcher or _yfinance_fetch
    raw = fetch(ticker, start, end)
    return tidy_equities(raw, ticker)


def fetch_many_equities(
    tickers: Sequence[str],
    start: date,
    end: date | None,
    *,
    fetcher: EquityFetcher | None = None,
) -> pd.DataFrame:
    frames = [fetch_equities(t, start, end, fetcher=fetcher) for t in tickers]
    if not frames:
        return _empty_tidy()
    return pd.concat(frames, ignore_index=True)


def tidy_equities(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw.empty:
        return _empty_tidy()

    df = raw.rename(columns=_RENAME).copy()

    missing = {"open", "high", "low", "close", "adj_close", "volume"} - set(df.columns)
    if missing:
        raise IngestError(f"{ticker}: upstream frame missing expected columns {sorted(missing)}")

    # Corporate-action columns are absent when a name has never paid a dividend
    # or split; treat that as zero rather than failing.
    for col in ("dividends", "stock_splits"):
        if col not in df.columns:
            df[col] = 0.0

    df["date"] = [ts.date() for ts in pd.DatetimeIndex(df.index)]
    df["ticker"] = ticker

    # A NaN in any core price/volume field means the bar is unusable; drop it
    # here so validation downstream can treat nulls as a hard error.
    df = df.dropna(subset=[*_PRICE_COLUMNS, "volume"])
    df = df.fillna({"dividends": 0.0, "stock_splits": 0.0})

    df[_PRICE_COLUMNS] = df[_PRICE_COLUMNS].astype("float64")
    df["volume"] = df["volume"].astype("int64")
    df[["dividends", "stock_splits"]] = df[["dividends", "stock_splits"]].astype("float64")

    return df[TIDY_COLUMNS].sort_values("date").reset_index(drop=True)


def _empty_tidy() -> pd.DataFrame:
    return pd.DataFrame({col: pd.Series(dtype=_dtype_for(col)) for col in TIDY_COLUMNS})


def _dtype_for(column: str) -> str:
    if column == "ticker":
        return "object"
    if column == "date":
        return "object"
    if column == "volume":
        return "int64"
    return "float64"


def _yfinance_fetch(ticker: str, start: date, end: date | None) -> pd.DataFrame:
    import yfinance as yf

    # auto_adjust=False keeps unadjusted OHLC alongside a separate Adj Close;
    # actions=True attaches the Dividends and Stock Splits columns.
    return yf.Ticker(ticker).history(
        start=start.isoformat(),
        end=end.isoformat() if end else None,
        auto_adjust=False,
        actions=True,
    )
