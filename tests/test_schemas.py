from datetime import date

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from mdp.validate.schemas import validate_equities, validate_macro


def _valid_equities() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
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


def _valid_macro() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": ["DGS10", "DGS10"],
            "date": [date(2023, 1, 2), date(2023, 1, 3)],
            "value": [3.79, 3.71],
        }
    )


def test_valid_equities_pass() -> None:
    out = validate_equities(_valid_equities())
    assert len(out) == 2


def test_valid_macro_pass() -> None:
    out = validate_macro(_valid_macro())
    assert len(out) == 2


def test_negative_volume_rejected() -> None:
    df = _valid_equities()
    df.loc[0, "volume"] = -5
    with pytest.raises(SchemaErrors):
        validate_equities(df)


def test_high_below_low_rejected() -> None:
    df = _valid_equities()
    df.loc[0, "high"] = 100.0  # now below low of 124.0
    with pytest.raises(SchemaErrors):
        validate_equities(df)


def test_null_close_rejected() -> None:
    df = _valid_equities()
    df.loc[0, "close"] = None
    with pytest.raises(SchemaErrors):
        validate_equities(df)


def test_negative_price_rejected() -> None:
    df = _valid_equities()
    df.loc[0, "open"] = -1.0
    with pytest.raises(SchemaErrors):
        validate_equities(df)


def test_duplicate_ticker_date_rejected() -> None:
    df = _valid_equities()
    df.loc[1, "date"] = date(2023, 1, 3)  # collide with row 0
    with pytest.raises(SchemaErrors):
        validate_equities(df)


def test_unexpected_column_rejected() -> None:
    df = _valid_equities()
    df["surprise"] = 1
    with pytest.raises(SchemaErrors):
        validate_equities(df)


def test_null_macro_value_rejected() -> None:
    df = _valid_macro()
    df.loc[0, "value"] = None
    with pytest.raises(SchemaErrors):
        validate_macro(df)


def test_negative_macro_value_allowed() -> None:
    # Real rates and net-flow series go negative; the schema must permit it.
    df = _valid_macro()
    df.loc[0, "value"] = -0.5
    out = validate_macro(df)
    assert out.loc[0, "value"] == -0.5
