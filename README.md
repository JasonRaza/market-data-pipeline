# market-data-pipeline

A small, local-first pipeline for **end-of-day market data**. It ingests daily
equity OHLCV bars and FRED macro series, validates them against explicit
schemas, lands them as partitioned Parquet, and loads them into DuckDB for
querying.

The emphasis is engineering quality — layered architecture, a validation
contract, idempotent loads, full type hints, tests, and CI — not feature
breadth. There is no order book and no intraday data; the scope is deliberately
tight and finishable.

```
fetch ──► validate ──► Parquet (raw landing) ──► DuckDB (analytical store) ──► SQL
```

## Architecture

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Config | `mdp.config` | Load the universe + date ranges from `config/tickers.yaml` into typed dataclasses. |
| Ingest | `mdp.ingest.equities` | Fetch equity OHLCV from yfinance → tidy frame. |
| Ingest | `mdp.ingest.macro` | Fetch FRED series via `fredapi` → tidy frame. |
| Validate | `mdp.validate.schemas` | pandera schemas; the data contract enforced before any write. |
| Storage | `mdp.storage.parquet` | Hive-partitioned Parquet landing (by ticker / series). |
| Storage | `mdp.storage.duckdb_store` | Idempotent `ON CONFLICT` upsert keyed on the natural key. |
| CLI | `mdp.cli` | `mdp ingest`, `mdp load`, `mdp query`. |

Each layer depends only on the ones below it, and the network calls are
injected so the ingest logic is unit-testable without hitting an API.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.12 (uv will fetch it).

```bash
uv sync                       # create the venv and install everything
uv run mdp --help
```

Macro ingestion needs a free [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html):

```bash
export FRED_API_KEY=your_key_here       # PowerShell: $env:FRED_API_KEY="..."
```

## Usage

Edit `config/tickers.yaml` to choose the universe and date ranges, then run the
three stages:

```bash
uv run mdp ingest                 # fetch -> validate -> land Parquet under ./data
uv run mdp load                   # read Parquet -> upsert into ./data/market.duckdb
uv run mdp query                  # run the built-in example query
```

Useful flags: `mdp ingest --no-macro` (skip FRED), `--config`, `--data-dir`,
`--db`. Loads are **idempotent** — re-running `ingest` + `load` will not create
duplicate rows.

### Example query

`mdp query` with no argument shows the latest bar per ticker:

```sql
SELECT ticker, date, close, adj_close
FROM equities
QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY date DESC) = 1
ORDER BY ticker;
```

Pass your own SQL as an argument — e.g. 30-day realized volatility of daily
returns, computed from the split/dividend-adjusted close:

```bash
uv run mdp query "
  WITH rets AS (
    SELECT ticker, date,
           ln(adj_close / lag(adj_close) OVER (PARTITION BY ticker ORDER BY date)) AS r
    FROM equities
  )
  SELECT ticker,
         stddev_samp(r) * sqrt(252) AS ann_vol
  FROM rets
  WHERE r IS NOT NULL
  GROUP BY ticker
  ORDER BY ann_vol DESC;
"
```

## Development

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy src tests        # type check
uv run pytest                # tests
```

CI (`.github/workflows/ci.yml`) runs all four on every push and pull request.

## Data model

**equities** — one row per `(ticker, date)`:
`open, high, low, close` (unadjusted), `adj_close` (back-adjusted),
`volume`, `dividends`, `stock_splits`. Both adjusted and unadjusted close are
kept so adjustment can be audited or recomputed.

**macro** — one row per `(series_id, date)`: `value`. Missing observations are
dropped rather than stored as nulls, keeping the series point-in-time clean.

## Limitations

This is a portfolio project, and the scope is intentionally narrow. Known
limitations, stated honestly:

- **Daily bars only.** No intraday, no order book, no tick data.
- **Provider trust.** `adj_close`, splits, and dividends are taken as given
  from yfinance; they are not cross-checked against a second vendor, and Yahoo
  occasionally revises history. The schema enforces internal consistency
  (`high >= low`, non-negative prices/volume), not external correctness.
- **No survivorship-bias handling.** The universe is whatever you list in the
  config; delisted tickers are not backfilled, so a naive backtest over this
  data would be survivorship-biased.
- **`adj_close` is point-in-time as of fetch.** Yahoo's adjustment factors
  change when new corporate actions occur, so re-ingesting later can revise past
  `adj_close` values. The upsert corrects them in place (which is the intended
  behavior) but means the store is not an immutable as-reported archive.
- **Single-machine, single-process.** DuckDB is an embedded analytical engine;
  there is no concurrent-writer story and no orchestration/scheduling layer.
- **FRED frequencies are not normalized.** Daily (`DGS10`) and monthly
  (`CPIAUCSL`) series live in the same table at their native frequency; joining
  them requires explicit resampling in SQL.
