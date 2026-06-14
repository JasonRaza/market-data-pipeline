from datetime import date

import pandas as pd

from mdp.storage.duckdb_store import (
    connect,
    row_count,
    upsert_equities,
    upsert_macro,
)


def _equities(close: float = 127.0, second_day: date = date(2023, 1, 4)) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
            "date": [date(2023, 1, 3), second_day],
            "open": [125.0, 126.0],
            "high": [128.0, 127.5],
            "low": [124.0, 125.5],
            "close": [close, 126.5],
            "adj_close": [126.1, 125.6],
            "volume": [1_000_000, 900_000],
            "dividends": [0.0, 0.23],
            "stock_splits": [0.0, 0.0],
        }
    )


def test_upsert_inserts_rows() -> None:
    con = connect()
    upsert_equities(con, _equities())
    assert row_count(con, "equities") == 2


def test_upsert_is_idempotent() -> None:
    con = connect()
    upsert_equities(con, _equities())
    upsert_equities(con, _equities())  # identical reload

    assert row_count(con, "equities") == 2  # no duplicates on (ticker, date)


def test_upsert_updates_existing_row_in_place() -> None:
    con = connect()
    upsert_equities(con, _equities(close=127.0))
    upsert_equities(con, _equities(close=130.0))  # revised close for same key

    assert row_count(con, "equities") == 2
    revised = con.execute(
        "SELECT close FROM equities WHERE ticker = 'AAPL' AND date = '2023-01-03'"
    ).fetchone()
    assert revised is not None
    assert revised[0] == 130.0


def test_upsert_appends_new_dates() -> None:
    con = connect()
    upsert_equities(con, _equities(second_day=date(2023, 1, 4)))
    upsert_equities(con, _equities(second_day=date(2023, 1, 5)))

    # Row (AAPL, 2023-01-03) overlaps; the new second day is appended -> 3 total.
    assert row_count(con, "equities") == 3


def test_macro_upsert_idempotent() -> None:
    con = connect()
    df = pd.DataFrame(
        {
            "series_id": ["DGS10", "DGS10"],
            "date": [date(2023, 1, 2), date(2023, 1, 3)],
            "value": [3.79, 3.71],
        }
    )
    upsert_macro(con, df)
    upsert_macro(con, df)

    assert row_count(con, "macro") == 2


def test_empty_upsert_is_noop() -> None:
    con = connect()
    assert upsert_equities(con, _equities().iloc[0:0]) == 0
    assert row_count(con, "equities") == 0
