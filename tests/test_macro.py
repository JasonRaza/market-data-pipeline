from datetime import date

import pandas as pd
import pytest

from mdp.ingest import IngestError
from mdp.ingest.macro import (
    API_KEY_ENV,
    TIDY_COLUMNS,
    fetch_macro,
    fetch_many_macro,
    tidy_macro,
)


def _fred_series() -> "pd.Series[float]":
    index = pd.DatetimeIndex(["2023-01-02", "2023-01-03", "2023-01-04"])
    return pd.Series([3.79, None, 3.71], index=index)


def test_tidy_reshapes_and_drops_nan() -> None:
    out = tidy_macro(_fred_series(), "DGS10")

    assert list(out.columns) == TIDY_COLUMNS
    # The middle observation was NaN and must not become a row.
    assert len(out) == 2
    assert out.loc[0, "date"] == date(2023, 1, 2)
    assert out.loc[1, "value"] == pytest.approx(3.71)
    assert (out["series_id"] == "DGS10").all()


def test_empty_after_dropna_returns_typed_empty() -> None:
    out = tidy_macro(pd.Series([None, None], dtype="float64"), "DGS10")

    assert list(out.columns) == TIDY_COLUMNS
    assert out.empty


def test_fetch_uses_injected_fetcher() -> None:
    def fake(series_id: str, start: date, end: date | None) -> "pd.Series[float]":
        return _fred_series()

    out = fetch_macro("DGS10", date(2023, 1, 1), None, fetcher=fake)

    assert (out["series_id"] == "DGS10").all()
    assert len(out) == 2


def test_fetch_many_concatenates() -> None:
    def fake(series_id: str, start: date, end: date | None) -> "pd.Series[float]":
        return _fred_series()

    out = fetch_many_macro(["DGS10", "CPIAUCSL"], date(2023, 1, 1), None, fetcher=fake)

    assert set(out["series_id"]) == {"DGS10", "CPIAUCSL"}
    assert len(out) == 4


def test_missing_api_key_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with pytest.raises(IngestError, match=API_KEY_ENV):
        fetch_macro("DGS10", date(2023, 1, 1), None)
