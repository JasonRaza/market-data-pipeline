# Project Guide

A from-scratch walkthrough of this pipeline: what every module does, how data
flows end to end, and *why* each engineering and finance decision was made. By
the end you should be able to rebuild this project and defend every choice in an
interview.

Read it top to bottom the first time. After that, the "How to explain this in an
interview" lines scattered throughout are your quick revision deck.

---

## 1. The big picture

The pipeline has one job: turn messy upstream market data into a clean, queryable
local store, reproducibly. It does that in three stages, each a separate CLI
command:

```
                ┌────────── mdp ingest ──────────┐   ┌── mdp load ──┐   ┌ mdp query ┐
 yfinance ─┐                                                                          
 FRED      ├─► fetch ─► validate (pandera) ─► Parquet ────► DuckDB upsert ────► SQL   
           │           (the contract)         (raw landing)  (analytical store)       
```

Two ideas drive the whole design:

1. **Layers that only know about the layer below them.** Ingest doesn't know
   about storage; storage doesn't know about the CLI. You can swap yfinance for
   another vendor by touching one function.
2. **A validation gate in the middle.** Nothing reaches storage unless it
   satisfies an explicit schema. Bad data fails loudly and early, not silently
   three steps later.

### Data flow, concretely

1. `mdp ingest` reads `config/tickers.yaml` → list of tickers, FRED series, date ranges.
2. For each ticker, it calls yfinance, reshapes the response into a **tidy frame**
   (one row per `(ticker, date)`), and concatenates them.
3. That frame is passed through a **pandera schema**. If any row violates the
   contract (null price, `high < low`, negative volume, duplicate key…), the run
   aborts with a detailed error. Nothing is written.
4. Valid data is written to **Parquet**, partitioned by ticker on disk.
5. `mdp load` reads the Parquet back and **upserts** it into a **DuckDB** table
   keyed on `(ticker, date)`. Re-running never duplicates rows.
6. `mdp query` runs arbitrary SQL against DuckDB.

---

## 2. Module-by-module tour

### `src/mdp/config.py` — typed configuration

Loads `config/tickers.yaml` into frozen dataclasses (`EquitiesConfig`,
`MacroConfig`, `Config`). It does two things beyond a raw `yaml.safe_load`:

- **Validates structure**: missing sections, empty ticker lists, and non-ISO
  dates raise a `ConfigError` with a message that says exactly what's wrong.
- **Coerces dates**: YAML parses unquoted dates into `date` objects and quoted
  ones into strings; `_coerce_date` accepts both so the file can be written
  either way.

*Why a dataclass instead of passing a dict around?* A dict gives you
`cfg["equities"]["tickers"]` and a `KeyError` at runtime if you typo it. A typed
dataclass gives you `cfg.equities.tickers`, autocompletion, and a mypy error at
*check* time. The config is the project's "front door" — making it typed means
every layer downstream gets real types for free.

> **Interview line:** "I parse config into frozen dataclasses so the rest of the
> codebase works with typed objects, not stringly-typed dicts — typos become
> type errors instead of runtime `KeyError`s."

### `src/mdp/ingest/equities.py` — equity OHLCV

The public surface is `fetch_equities` / `fetch_many_equities`, and the real
work is in `tidy_equities(raw, ticker)`, which reshapes a yfinance-shaped frame
into the tidy schema:

```
ticker, date, open, high, low, close, adj_close, volume, dividends, stock_splits
```

Key decisions:

- **Dependency injection for the network call.** `fetch_equities(..., fetcher=...)`
  takes the function that actually hits yfinance. The default is the real one;
  tests pass a fake that returns a canned frame. This is why the ingest tests run
  offline, deterministically, in milliseconds.
- **Keep both `close` and `adj_close`, plus `dividends`/`stock_splits`.** See the
  finance section — this is the most important data-modeling choice in the project.
- **Drop rows with null core fields, then cast types.** A NaN in OHLCV means the
  bar is unusable, so we drop it here; that lets the validation layer treat *any*
  remaining null as a hard error rather than something to tolerate.

### `src/mdp/ingest/macro.py` — FRED macro series

Same shape as equities: `fetch_macro` / `fetch_many_macro`, injected fetcher,
and `tidy_macro` producing `series_id, date, value`.

Two specifics:

- **NaNs are dropped, not stored.** FRED returns a value for every date in the
  range with `NaN` where there's no observation. A missing observation is the
  *absence of a row*, not a null — storing nulls would pollute later joins and
  point-in-time logic.
- **The API key is resolved once and fails loudly.** `_build_fred_fetcher` reads
  `FRED_API_KEY` and raises `IngestError` immediately if it's missing, before any
  work — rather than failing halfway through a multi-series fetch.

### `src/mdp/validate/schemas.py` — the data contract

Two pandera `DataFrameSchema`s and two thin `validate_*` functions. This is the
gate: `mdp ingest` calls `validate_equities` *before* `write_equities`.

The equities schema enforces:

- Correct dtypes and **no nulls** anywhere.
- **Non-negative** prices and volume.
- Row-level cross-checks: `high >= low`, `high >= open`, `high >= close`,
  `low <= open`, `low <= close`.
- **Uniqueness** on `(ticker, date)` — no two bars for the same key.
- `strict=True` — an unexpected column is an error, so schema drift is caught.

One subtle, deliberate exclusion: **the high/low bounds apply only to the
unadjusted OHLC, never to `adj_close`.** Back-adjusted close can legitimately sit
outside a historical day's high/low range (see §4), so bounding it would reject
valid data. The macro schema deliberately leaves `value` unbounded because many
series go negative.

> **Interview line:** "Validation is a contract enforced before any write. The
> schema encodes domain invariants — `high >= low`, non-negative volume, unique
> keys — and `strict=True` catches schema drift. Crucially I *don't* bound
> `adj_close` by the day's range, because back-adjustment can move it outside."

### `src/mdp/storage/parquet.py` — raw landing

Writes tidy frames to Hive-partitioned Parquet: `data/equities/ticker=AAPL/…`,
`data/macro/series_id=DGS10/…`.

- **Partitioning by ticker/series** means a single name can be rewritten in
  isolation, and a query that filters by ticker only reads that partition
  (*partition pruning*).
- **`existing_data_behavior="delete_matching"`** makes the *landing* idempotent:
  re-landing a partition replaces its files instead of appending duplicates.
- Reading normalizes the partition column back to a plain string and restores
  canonical column order, so a round-trip is lossless (dates come back as Python
  `date`, not tz-aware timestamps).

*Why Parquet at all, if it's going into DuckDB anyway?* Parquet is the durable,
columnar, vendor-neutral landing zone — the "raw" tier. If the DuckDB file is
deleted, you rebuild it from Parquet without re-hitting the network. Separating
"raw landing" from "analytical store" is a standard data-engineering pattern
(bronze/silver).

### `src/mdp/storage/duckdb_store.py` — analytical store + idempotent upsert

Creates two tables with composite **primary keys** (`(ticker, date)`,
`(series_id, date)`) and loads via:

```sql
INSERT INTO equities BY NAME SELECT * FROM incoming
ON CONFLICT (ticker, date) DO UPDATE SET open = excluded.open, …
```

This is the heart of **idempotency**. Re-running the pipeline:

- an unchanged bar → a no-op update,
- a revised bar (e.g. Yahoo restated it) → corrected in place,
- a genuinely new date → inserted.

The row count never grows from re-running the same range. The incoming frame is
handed to DuckDB as an **Arrow table** so the Python `date` column lands as a
real DuckDB `DATE` (an object-dtype pandas column would otherwise be read as
text) and columns match `BY NAME` rather than by position.

> **Interview line:** "Loads are idempotent: a composite primary key plus
> `INSERT … ON CONFLICT DO UPDATE` means re-ingesting the same data updates in
> place instead of duplicating. There's an explicit test that loads twice and
> asserts the row count is unchanged."

### `src/mdp/cli.py` — the three commands

`typer` app exposing `ingest`, `load`, `query`. It's pure orchestration — it
calls config → ingest → validate → storage in order and contains no business
logic itself. A missing FRED key surfaces as a clean CLI error, not a traceback.

---

## 3. The software-engineering concepts (and why)

### Separation of concerns / layered architecture
Each module has one responsibility and depends only downward (CLI → storage →
validate → ingest → config). The payoff is **change isolation**: swapping the
price vendor touches one fetcher; changing the storage engine touches one module.
The injected fetchers are the seam that makes this testable.
> **Interview line:** "Layered architecture with dependency injection at the I/O
> boundary — so the parts that touch the network are the only parts that can't be
> unit-tested in isolation, and even those are behind an injectable seam."

### Idempotency
Running the pipeline twice produces the same state as running it once. Achieved
at two tiers: `delete_matching` in Parquet and `ON CONFLICT DO UPDATE` in DuckDB.
This is what makes the pipeline safe to schedule and safe to retry after a crash.
> **Interview line:** "Idempotent by construction — natural-key upsert plus
> partition-replace — so retries and re-runs can't corrupt or duplicate data."

### Schema validation as a contract
pandera schemas are an executable specification of what valid data looks like,
enforced *before* persistence. It turns "garbage in, garbage out" into "garbage
in, loud failure." The schema lives in code, is version-controlled, and is tested.
> **Interview line:** "I treat the schema as a contract at the storage boundary —
> validation runs before any write, so invariants downstream consumers rely on
> are guaranteed, and bad data fails fast with a precise error."

### Dependency management with `uv`
`pyproject.toml` declares dependencies; `uv.lock` pins exact resolved versions;
`uv sync --frozen` reproduces the environment byte-for-byte. CI uses `--frozen`
so a dependency can't silently change under us. `uv` also manages the Python
interpreter itself (3.12), so "works on my machine" extends to CI.
> **Interview line:** "`uv` gives me a committed lockfile and reproducible installs,
> including the Python version — CI builds the exact same environment I develop in."

### Testing strategy
Tests target the parts most likely to break and most expensive to debug in
production: parsing/reshaping (`tidy_*`), the validation rules (including explicit
**rejection** tests — negative volume, `high < low`, nulls, duplicates), storage
**idempotency**, and an end-to-end CLI flow with the network mocked. Network code
is injected, so the suite is fast and deterministic — no live API in CI.
> **Interview line:** "I test behavior, not implementation: schema-rejection tests
> prove the contract bites, an idempotency test proves re-loads don't duplicate,
> and the CLI test exercises the whole flow with the fetchers mocked."

### Type checking with mypy
Full type hints on every function; `mypy` runs in CI with untyped-def checks on.
Types are machine-checked documentation and catch a class of bugs (wrong shapes,
`None` handling) before runtime. Third-party libs without stubs are explicitly
marked `ignore_missing_imports` rather than papered over with inline ignores.
> **Interview line:** "Types are enforced in CI, not aspirational. Where a library
> ships no stubs I scope the `ignore_missing_imports` to that module instead of
> sprinkling `# type: ignore`."

### Continuous integration
`.github/workflows/ci.yml` runs ruff (lint), ruff format `--check`, mypy, and
pytest on every push and PR. CI is the safety net that keeps `main` always
green and makes the quality bar non-negotiable rather than a matter of discipline.
> **Interview line:** "CI runs the same four checks I run locally, so `main` is
> always lint-clean, type-clean, and green — the standard is enforced, not hoped for."

---

## 4. The quant / finance concepts (and why)

### OHLCV bars
A daily **bar** summarizes a trading day in five numbers: **O**pen, **H**igh,
**L**ow, **C**lose, **V**olume. It's the atomic unit of end-of-day data — a lossy
but standard compression of the day's trading. Most research, signals, and
backtests start from bars.
> **Interview line:** "An OHLCV bar is the standard daily summary of price action —
> open/high/low/close plus volume — and it's the base unit almost every EOD
> strategy is built on."

### Adjusted vs unadjusted close — *the* key idea
The **unadjusted close** is the actual price printed that day. The **adjusted
close** rewrites history so that splits and dividends don't show up as fake price
jumps:

- On a **2-for-1 split**, the raw price halves overnight. Nothing was lost — you
  own twice the shares — but a naive return series sees a -50% "crash." Adjustment
  scales pre-split prices down so the return is ~0%.
- On a **dividend**, the price drops by roughly the dividend amount ex-date. That's
  not a loss either; you got cash. Adjustment folds it back in.

**Use which, when?** Compute **returns and signals from `adj_close`** — it's the
total-return, apples-to-apples series. Use **unadjusted `close`** when you need the
actual traded price (e.g. modeling realistic fills or matching a brokerage
statement). This pipeline stores **both**, plus the raw `dividends`/`stock_splits`
events, so you're never stuck — you can audit the adjustment or redo it.

This is also why the schema does **not** bound `adj_close` by the day's high/low:
adjustment scales historical prices, so a past `adj_close` can sit *below* that
day's recorded low. Bounding it would reject correct data.
> **Interview line:** "Adjusted close folds splits and dividends back in so returns
> reflect total return, not artificial jumps. I compute returns from adjusted close
> but keep the unadjusted close for actual traded prices — and I store the raw
> corporate-action columns so adjustment is auditable, not a black box."

### Corporate actions (splits & dividends) and why they matter
Splits and dividends are the corporate actions that make raw prices
discontinuous. If you ignore them, every dividend looks like a small loss and
every split looks like a crash — your backtest's returns are simply wrong. Storing
the events themselves (not just the adjusted series) means you can reconstruct or
verify the adjustment factor at any point.
> **Interview line:** "Splits and dividends introduce discontinuities in raw price;
> mishandling them silently corrupts every return downstream, so I keep the raw
> events alongside the adjusted series."

### Point-in-time / no-lookahead integrity
A backtest is only honest if, at each historical date, it uses **only information
available then**. Two traps this project is mindful of:

- **Revisions.** Yahoo's `adj_close` changes whenever a *new* corporate action
  occurs, so today's `adj_close` for a 2019 date is not what you'd have seen in
  2019. The upsert deliberately *corrects* values in place — good for a current
  snapshot, but it means this store is not an as-reported archive (called out in
  the README's limitations).
- **Macro release lags.** FRED stamps an observation with the *period* it
  describes (e.g. CPI for January), but that number is only *published* weeks
  later. Treating the period date as the date you "knew" it introduces lookahead.

Getting this wrong produces backtests that look brilliant and fail live — the
single most common way quant research lies to itself.
> **Interview line:** "No-lookahead means only using data you'd actually have had at
> each point in time. The two classic leaks are restated history and macro release
> lags — I document both as limitations rather than pretend the snapshot is
> point-in-time accurate."

### What the FRED macro series represent
[FRED](https://fred.stlouisfed.org/) is the St. Louis Fed's free economic data
warehouse. The defaults in the config:

- **`DGS10`** — 10-Year Treasury constant-maturity yield, **daily**, in percent.
  A core "risk-free rate" / rates proxy.
- **`CPIAUCSL`** — Consumer Price Index (all urban consumers), **monthly**,
  seasonally adjusted. The standard inflation gauge.

They sit in one table at their **native frequencies** — daily and monthly are not
resampled together — so joining them to daily equity data requires explicit
alignment in SQL. That's a deliberate "don't silently transform" choice.
> **Interview line:** "DGS10 is the 10-year Treasury yield (daily) and CPIAUCSL is
> headline CPI (monthly). I store them at native frequency and resample explicitly
> in queries rather than baking in a frequency conversion the user didn't ask for."

---

## 5. How you'd rebuild it

1. `uv init`, set `requires-python`, add deps; configure ruff/mypy/pytest in `pyproject.toml`.
2. `config.py`: dataclasses + a validating loader.
3. `ingest/*.py`: a `tidy_*` reshaper plus an injected fetcher, per source.
4. `validate/schemas.py`: pandera schemas encoding the invariants; thin validate functions.
5. `storage/parquet.py`: partitioned write with `delete_matching`; lossless read.
6. `storage/duckdb_store.py`: PK tables + `ON CONFLICT DO UPDATE`.
7. `cli.py`: wire the stages together with typer; keep logic out of it.
8. Tests alongside each module — especially rejection and idempotency tests.
9. CI running ruff + mypy + pytest.
10. A README and this guide.

If you can explain *why* at each step — layering, the validation gate, idempotent
upsert, adjusted vs unadjusted close, no-lookahead — you can defend the whole
project.
