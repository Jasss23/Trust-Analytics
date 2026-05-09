"""Load provided Pluang CSVs into local SQLite."""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pluang_agent.db import connect, quote_identifier, table_counts

REQUIRED_CSVS = [
    "raw_transactions.csv",
    "dim_users.csv",
    "aum_monthly_snapshot.csv",
    "stg_mixpanel_events.csv",
    "fct_trading_daily.csv",
    "agg_monthly_biz_summary.csv",
    "mart_ops_dashboard.csv",
]


@dataclass(frozen=True)
class LoadResult:
    db_path: Path
    data_dir: Path
    row_counts: dict[str, int]


class DataLoadError(RuntimeError):
    """Raised when local data cannot be loaded."""


def table_name_for_csv(csv_path: Path) -> str:
    return csv_path.stem


def validate_data_dir(data_dir: Path) -> None:
    missing = [name for name in REQUIRED_CSVS if not (data_dir / name).is_file()]
    if missing:
        raise DataLoadError(
            f"Missing required CSV file(s) in {data_dir}: {', '.join(sorted(missing))}"
        )


def load_csvs(data_dir: Path, db_path: Path) -> LoadResult:
    """Load all required CSVs into SQLite, replacing target tables each run."""
    data_dir = data_dir.expanduser().resolve()
    db_path = db_path.expanduser()
    validate_data_dir(data_dir)

    with connect(db_path) as conn:
        for csv_name in REQUIRED_CSVS:
            _load_one_csv(conn, data_dir / csv_name)
        counts = table_counts(conn, [Path(name).stem for name in REQUIRED_CSVS])
    return LoadResult(db_path=db_path, data_dir=data_dir, row_counts=counts)


def _load_one_csv(conn: sqlite3.Connection, csv_path: Path) -> None:
    table_name = table_name_for_csv(csv_path)
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise DataLoadError(f"CSV has no header row: {csv_path}")

        columns = [col.strip() for col in reader.fieldnames]
        if any(not col for col in columns):
            raise DataLoadError(f"CSV has an empty column name: {csv_path}")

        quoted_table = quote_identifier(table_name)
        quoted_columns = [quote_identifier(col) for col in columns]

        conn.execute(f"DROP TABLE IF EXISTS {quoted_table}")
        column_sql = ", ".join(f"{col} TEXT" for col in quoted_columns)
        conn.execute(f"CREATE TABLE {quoted_table} ({column_sql})")

        placeholders = ", ".join("?" for _ in columns)
        insert_sql = (
            f"INSERT INTO {quoted_table} ({', '.join(quoted_columns)}) "
            f"VALUES ({placeholders})"
        )
        conn.executemany(insert_sql, ([row.get(col, "") for col in columns] for row in reader))

