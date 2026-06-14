"""pandera schemas that act as the data contract for the tidy frames.

Validation is the gate in front of storage: nothing is written unless it
satisfies these schemas, so downstream consumers (Parquet, DuckDB, any future
backtest) can assume the invariants hold.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pandera.pandas as pa

_is_date = pa.Check(lambda v: isinstance(v, date), element_wise=True, error="not a date")

# Note: the high/low bounds are checked against the UNADJUSTED OHLC only.
# adj_close is back-adjusted for splits and dividends, so for historical bars it
# can legitimately fall outside that day's high/low range and must not be bounded by it.
EQUITIES_SCHEMA = pa.DataFrameSchema(
    columns={
        "ticker": pa.Column(str, nullable=False),
        "date": pa.Column(object, checks=_is_date, nullable=False),
        "open": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
        "high": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
        "low": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
        "close": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
        "adj_close": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
        "volume": pa.Column(int, checks=pa.Check.ge(0), nullable=False),
        "dividends": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
        "stock_splits": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
    },
    checks=[
        pa.Check(lambda df: df["high"] >= df["low"], error="high < low"),
        pa.Check(lambda df: df["high"] >= df["open"], error="high < open"),
        pa.Check(lambda df: df["high"] >= df["close"], error="high < close"),
        pa.Check(lambda df: df["low"] <= df["open"], error="low > open"),
        pa.Check(lambda df: df["low"] <= df["close"], error="low > close"),
    ],
    unique=["ticker", "date"],
    strict=True,
)

# value is intentionally unbounded: many FRED series can be negative or zero
# (real rates, net flows), so a sign or range check would be wrong here.
MACRO_SCHEMA = pa.DataFrameSchema(
    columns={
        "series_id": pa.Column(str, nullable=False),
        "date": pa.Column(object, checks=_is_date, nullable=False),
        "value": pa.Column(float, nullable=False),
    },
    unique=["series_id", "date"],
    strict=True,
)


def validate_equities(df: pd.DataFrame) -> pd.DataFrame:
    return EQUITIES_SCHEMA.validate(df, lazy=True)


def validate_macro(df: pd.DataFrame) -> pd.DataFrame:
    return MACRO_SCHEMA.validate(df, lazy=True)
