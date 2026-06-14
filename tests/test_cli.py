from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from mdp import cli

runner = CliRunner()


def _equities_df(*_args: object, **_kwargs: object) -> pd.DataFrame:
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


def _macro_df(*_args: object, **_kwargs: object) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": ["DGS10", "DGS10"],
            "date": [date(2023, 1, 2), date(2023, 1, 3)],
            "value": [3.79, 3.71],
        }
    )


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "tickers.yaml"
    path.write_text(
        'equities:\n  tickers: [AAPL]\n  start: "2023-01-01"\n  end: null\n'
        'macro:\n  series: [DGS10]\n  start: "2023-01-01"\n  end: null\n',
        encoding="utf-8",
    )
    return path


def test_ingest_load_query_flow(
    tmp_path: Path, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fetch_many_equities", _equities_df)
    monkeypatch.setattr(cli, "fetch_many_macro", _macro_df)

    data_dir = tmp_path / "data"
    db = tmp_path / "market.duckdb"

    ingest = runner.invoke(
        cli.app,
        ["ingest", "--config", str(config_file), "--data-dir", str(data_dir)],
    )
    assert ingest.exit_code == 0, ingest.output
    assert "equities: landed 2 bars" in ingest.output
    assert (data_dir / "equities" / "ticker=AAPL").is_dir()

    load = runner.invoke(cli.app, ["load", "--data-dir", str(data_dir), "--db", str(db)])
    assert load.exit_code == 0, load.output
    assert "upserted 2 equity bars and 2 macro observations" in load.output

    q = runner.invoke(
        cli.app, ["query", "SELECT count(*) AS n FROM equities", "--db", str(db)]
    )
    assert q.exit_code == 0, q.output
    assert "2" in q.output


def test_ingest_macro_missing_key_is_clean_error(
    tmp_path: Path, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(cli, "fetch_many_equities", _equities_df)

    result = runner.invoke(
        cli.app,
        [
            "ingest",
            "--config",
            str(config_file),
            "--data-dir",
            str(tmp_path / "data"),
            "--no-equities",
        ],
    )
    assert result.exit_code != 0
    assert "FRED_API_KEY" in result.output
