"""Raw landing layer: write/read tidy frames as Hive-partitioned Parquet.

Equities are partitioned by ``ticker`` and macro by ``series_id`` so that a
single name can be re-fetched and rewritten in isolation. Writes use
``delete_matching`` so re-landing a partition replaces it rather than appending
duplicate files — the landing layer is itself idempotent at partition grain.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from mdp.ingest import equities, macro

_EQUITIES_DIR = "equities"
_MACRO_DIR = "macro"


def write_equities(df: pd.DataFrame, root: Path | str) -> Path:
    return _write(df, root, subdir=_EQUITIES_DIR, partition_col="ticker")


def write_macro(df: pd.DataFrame, root: Path | str) -> Path:
    return _write(df, root, subdir=_MACRO_DIR, partition_col="series_id")


def read_equities(root: Path | str) -> pd.DataFrame:
    return _read(root, subdir=_EQUITIES_DIR, columns=equities.TIDY_COLUMNS, partition_col="ticker")


def read_macro(root: Path | str) -> pd.DataFrame:
    return _read(root, subdir=_MACRO_DIR, columns=macro.TIDY_COLUMNS, partition_col="series_id")


def _write(df: pd.DataFrame, root: Path | str, *, subdir: str, partition_col: str) -> Path:
    base = Path(root) / subdir
    if df.empty:
        return base

    base.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    ds.write_dataset(
        table,
        base_dir=str(base),
        format="parquet",
        partitioning=[partition_col],
        partitioning_flavor="hive",
        existing_data_behavior="delete_matching",
    )
    return base


def _read(root: Path | str, *, subdir: str, columns: list[str], partition_col: str) -> pd.DataFrame:
    base = Path(root) / subdir
    if not base.exists():
        return pd.DataFrame({col: pd.Series(dtype=_empty_dtype(col)) for col in columns})

    table = ds.dataset(str(base), format="parquet", partitioning="hive").to_table()
    df = table.to_pandas()
    # The Hive partition column comes back as a dictionary/category; normalize
    # it to plain strings and restore the canonical column order.
    df[partition_col] = df[partition_col].astype(str)
    return df[columns].reset_index(drop=True)


def _empty_dtype(column: str) -> str:
    if column == "volume":
        return "int64"
    if column in {"ticker", "series_id", "date"}:
        return "object"
    return "float64"
