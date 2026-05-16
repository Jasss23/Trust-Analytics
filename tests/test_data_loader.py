from __future__ import annotations

import csv
from pathlib import Path

import pytest

from trust_analytics.data_loader import REQUIRED_CSVS, DataLoadError, load_csvs
from trust_analytics.db import connect, table_count


def test_load_csvs_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for csv_name in REQUIRED_CSVS:
        _write_csv(data_dir / csv_name, [{"id": "1"}, {"id": "2"}])

    db_path = tmp_path / "nested" / "trust_analytics.sqlite"
    first = load_csvs(data_dir, db_path)
    second = load_csvs(data_dir, db_path)

    assert first.row_counts == second.row_counts
    assert set(second.row_counts.values()) == {2}
    with connect(db_path) as conn:
        assert table_count(conn, "raw_transactions") == 2


def test_load_csvs_reports_missing_files(tmp_path: Path) -> None:
    with pytest.raises(DataLoadError, match="Missing required CSV"):
        load_csvs(tmp_path, tmp_path / "trust_analytics.sqlite")


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
