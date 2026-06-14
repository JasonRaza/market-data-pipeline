"""Fetch macro time series from FRED and reshape into a tidy frame.

The tidy schema is one row per (series_id, date):

    series_id, date, value

FRED returns observations as a date-indexed Series with NaN for periods that
have no published value. We drop those NaNs here: a missing observation is the
absence of a row, not a null, which keeps the stored series point-in-time clean.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import date

import pandas as pd

from mdp.ingest import IngestError

TIDY_COLUMNS: list[str] = ["series_id", "date", "value"]

API_KEY_ENV = "FRED_API_KEY"

# Injected unit of work: maps a series id + range to a date-indexed Series.
MacroFetcher = Callable[[str, date, date | None], "pd.Series[float]"]


def fetch_macro(
    series_id: str,
    start: date,
    end: date | None,
    *,
    fetcher: MacroFetcher | None = None,
) -> pd.DataFrame:
    fetch = fetcher or _build_fred_fetcher()
    raw = fetch(series_id, start, end)
    return tidy_macro(raw, series_id)


def fetch_many_macro(
    series_ids: Sequence[str],
    start: date,
    end: date | None,
    *,
    fetcher: MacroFetcher | None = None,
) -> pd.DataFrame:
    # Resolve the fetcher once so a single missing key fails before any work.
    fetch = fetcher or _build_fred_fetcher()
    frames = [fetch_macro(s, start, end, fetcher=fetch) for s in series_ids]
    if not frames:
        return _empty_tidy()
    return pd.concat(frames, ignore_index=True)


def tidy_macro(raw: pd.Series[float], series_id: str) -> pd.DataFrame:
    series = raw.dropna()
    if series.empty:
        return _empty_tidy()

    return pd.DataFrame(
        {
            "series_id": series_id,
            "date": [ts.date() for ts in pd.DatetimeIndex(series.index)],
            "value": series.to_numpy(dtype="float64"),
        }
    )


def _empty_tidy() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": pd.Series(dtype="object"),
            "date": pd.Series(dtype="object"),
            "value": pd.Series(dtype="float64"),
        }
    )


def _build_fred_fetcher() -> MacroFetcher:
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise IngestError(
            f"{API_KEY_ENV} is not set; export a free FRED API key to ingest macro series"
        )

    def fetch(series_id: str, start: date, end: date | None) -> pd.Series[float]:
        from fredapi import Fred

        fred = Fred(api_key=api_key)
        return fred.get_series(
            series_id,
            observation_start=start.isoformat(),
            observation_end=end.isoformat() if end else None,
        )

    return fetch
