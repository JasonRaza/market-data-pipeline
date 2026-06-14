"""Command-line entry point: ``mdp ingest``, ``mdp load``, ``mdp query``.

The pipeline is three explicit stages so each can be run and reasoned about
independently:

    ingest  fetch -> validate -> land as Parquet
    load    read Parquet -> idempotent upsert into DuckDB
    query   run SQL against the DuckDB store
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from mdp.config import load_config
from mdp.ingest import IngestError
from mdp.ingest.equities import fetch_many_equities
from mdp.ingest.macro import fetch_many_macro
from mdp.storage import duckdb_store, parquet
from mdp.validate.schemas import validate_equities, validate_macro

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

_DEFAULT_CONFIG = Path("config/tickers.yaml")
_DEFAULT_DATA_DIR = Path("data")
_DEFAULT_DB = Path("data/market.duckdb")

_EXAMPLE_QUERY = """
SELECT ticker, date, close, adj_close
FROM equities
QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY date DESC) = 1
ORDER BY ticker
"""


@app.command()
def ingest(
    config: Annotated[Path, typer.Option(help="Path to tickers.yaml.")] = _DEFAULT_CONFIG,
    data_dir: Annotated[Path, typer.Option(help="Parquet landing root.")] = _DEFAULT_DATA_DIR,
    equities: Annotated[bool, typer.Option(help="Ingest equity OHLCV.")] = True,
    macro: Annotated[bool, typer.Option(help="Ingest FRED macro series.")] = True,
) -> None:
    """Fetch, validate, and land raw data as Parquet."""
    cfg = load_config(config)

    if equities:
        df = fetch_many_equities(cfg.equities.tickers, cfg.equities.start, cfg.equities.end)
        df = validate_equities(df)
        parquet.write_equities(df, data_dir)
        typer.echo(f"equities: landed {len(df)} bars for {df['ticker'].nunique()} tickers")

    if macro:
        try:
            df = fetch_many_macro(cfg.macro.series, cfg.macro.start, cfg.macro.end)
        except IngestError as exc:
            raise typer.BadParameter(str(exc)) from exc
        df = validate_macro(df)
        parquet.write_macro(df, data_dir)
        typer.echo(f"macro: landed {len(df)} observations for {df['series_id'].nunique()} series")


@app.command()
def load(
    data_dir: Annotated[Path, typer.Option(help="Parquet landing root.")] = _DEFAULT_DATA_DIR,
    db: Annotated[Path, typer.Option(help="DuckDB database file.")] = _DEFAULT_DB,
) -> None:
    """Load landed Parquet into DuckDB via idempotent upsert."""
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb_store.connect(db)
    try:
        eq = parquet.read_equities(data_dir)
        n_eq = duckdb_store.upsert_equities(con, eq)
        mac = parquet.read_macro(data_dir)
        n_mac = duckdb_store.upsert_macro(con, mac)
    finally:
        con.close()
    typer.echo(f"upserted {n_eq} equity bars and {n_mac} macro observations into {db}")


@app.command()
def query(
    sql: Annotated[str | None, typer.Argument(help="SQL to run; omit for an example.")] = None,
    db: Annotated[Path, typer.Option(help="DuckDB database file.")] = _DEFAULT_DB,
) -> None:
    """Run a SQL query against the DuckDB store."""
    con = duckdb_store.connect(db)
    try:
        result = con.execute(sql or _EXAMPLE_QUERY).df()
    finally:
        con.close()

    if result.empty:
        typer.echo("(no rows)")
    else:
        typer.echo(result.to_string(index=False))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
