"""Analytical store: idempotent upserts into DuckDB keyed on the natural key.

Each table has a composite primary key — (ticker, date) for equities and
(series_id, date) for macro — and loads use ``INSERT ... ON CONFLICT DO UPDATE``.
Re-running ingestion therefore never duplicates rows: an unchanged bar is a
no-op update, a revised bar is corrected in place, and a new bar is inserted.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa

_EQUITIES_UPDATE_COLS = [
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "dividends",
    "stock_splits",
]
_MACRO_UPDATE_COLS = ["value"]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS equities (
    ticker VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    adj_close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    dividends DOUBLE NOT NULL,
    stock_splits DOUBLE NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS macro (
    series_id VARCHAR NOT NULL,
    date DATE NOT NULL,
    value DOUBLE NOT NULL,
    PRIMARY KEY (series_id, date)
);
"""


def connect(db_path: Path | str = ":memory:") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path))
    init_schema(con)
    return con


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SCHEMA_SQL)


def upsert_equities(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    return _upsert(
        con, df, table="equities", conflict=("ticker", "date"), update_cols=_EQUITIES_UPDATE_COLS
    )


def upsert_macro(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    return _upsert(
        con, df, table="macro", conflict=("series_id", "date"), update_cols=_MACRO_UPDATE_COLS
    )


def row_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    result = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert result is not None  # COUNT always returns a row
    return int(result[0])


def _upsert(
    con: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    *,
    table: str,
    conflict: tuple[str, ...],
    update_cols: list[str],
) -> int:
    if df.empty:
        return 0

    # Register as Arrow so the python date column lands as DuckDB DATE (object
    # dtype would otherwise be read as text) and column matching is by name.
    incoming = pa.Table.from_pandas(df, preserve_index=False)
    con.register("incoming", incoming)
    try:
        set_clause = ", ".join(f"{col} = excluded.{col}" for col in update_cols)
        con.execute(
            f"INSERT INTO {table} BY NAME SELECT * FROM incoming "
            f"ON CONFLICT ({', '.join(conflict)}) DO UPDATE SET {set_clause}"
        )
    finally:
        con.unregister("incoming")
    return len(df)
