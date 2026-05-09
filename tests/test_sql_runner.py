from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pluang_agent.sql_runner import SQLSafetyError, execute_read_only, validate_read_only_sql


def test_read_only_select_executes(tmp_path: Path) -> None:
    db_path = tmp_path / "test.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sample (id TEXT)")
        conn.execute("INSERT INTO sample VALUES ('a')")

    rows = execute_read_only(db_path, "SELECT id FROM sample")

    assert rows == [{"id": "a"}]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM sample",
        "UPDATE sample SET id = 'b'",
        "DROP TABLE sample",
        "PRAGMA table_info(sample)",
        "SELECT 1; SELECT 2",
    ],
)
def test_mutating_or_multi_statement_sql_is_rejected(sql: str) -> None:
    with pytest.raises(SQLSafetyError):
        validate_read_only_sql(sql)
