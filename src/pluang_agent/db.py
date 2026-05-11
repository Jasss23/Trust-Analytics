"""SQLite utilities for the local analytics database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def quote_identifier(name: str) -> str:
    """Safely quote a SQLite identifier."""
    return '"' + name.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_count(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {quote_identifier(table_name)}").fetchone()
    return int(row["n"])


def table_counts(conn: sqlite3.Connection, table_names: Iterable[str]) -> dict[str, int]:
    return {name: table_count(conn, name) for name in table_names}


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def describe_table(conn: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA table_info({quote_identifier(table_name)})").fetchall()
    return [dict(row) for row in rows]


def sqlite_schema_context(conn: sqlite3.Connection) -> str:
    """Return compact schema context for prompts and debug output."""
    lines: list[str] = []
    for table in list_tables(conn):
        columns = [row["name"] for row in describe_table(conn, table)]
        lines.append(f"- {table}({', '.join(columns)})")
    return "\n".join(lines)


def columns_for_tables(db_path: Path, table_names: Iterable[str]) -> str:
    """Render live PRAGMA table_info output for the given table names.

    Used by R5's schema-grounded retry: when the SQL Agent's previous SQL
    failed with "no such column X" or similar, this helper produces a
    compact text block that the correction prompt can inject so the agent
    sees the actual columns and types rather than guessing.

    Unknown table names are reported explicitly so the agent's correction
    can choose another table.
    """
    if not table_names:
        return "(no tables referenced — schema_hint omitted)"
    seen: list[str] = []
    # Deduplicate while preserving order
    for name in table_names:
        if name and name not in seen:
            seen.append(name)
    lines: list[str] = []
    with connect(db_path) as conn:
        existing = set(list_tables(conn))
        for name in seen:
            if name not in existing:
                lines.append(f"- {name}: TABLE NOT FOUND")
                continue
            cols = describe_table(conn, name)
            col_specs = [f"{c['name']} ({c['type']})" for c in cols]
            lines.append(f"- {name}({', '.join(col_specs)})")
    return "\n".join(lines)
