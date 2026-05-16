"""Read-only SQL execution guardrails."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from trust_analytics.db import connect

MUTATING_KEYWORDS = {
    "alter",
    "attach",
    "create",
    "delete",
    "detach",
    "drop",
    "insert",
    "pragma",
    "replace",
    "update",
    "vacuum",
}


class SQLSafetyError(RuntimeError):
    """Raised when a query violates read-only execution rules."""


def validate_read_only_sql(sql: str) -> None:
    stripped = sql.strip()
    if not stripped:
        raise SQLSafetyError("SQL is empty.")
    if sqlite3.complete_statement(stripped + ("" if stripped.endswith(";") else ";")):
        parts = [part for part in stripped.split(";") if part.strip()]
        if len(parts) > 1:
            raise SQLSafetyError("Only a single SQL statement is allowed.")
    first = re.match(r"^\s*([a-zA-Z]+)", stripped)
    if not first or first.group(1).lower() not in {"select", "with"}:
        raise SQLSafetyError("Only SELECT or WITH queries are allowed.")
    tokens = {token.lower() for token in re.findall(r"[A-Za-z_]+", stripped)}
    blocked = sorted(tokens & MUTATING_KEYWORDS)
    if blocked:
        raise SQLSafetyError(f"Mutating or unsafe SQL keyword(s) are not allowed: {blocked}")


def execute_read_only(db_path: Path, sql: str) -> list[dict[str, Any]]:
    validate_read_only_sql(sql)
    try:
        with connect(db_path) as conn:
            rows = conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        raise SQLSafetyError(f"SQLite execution failed: {exc}") from exc
    return [dict(row) for row in rows]
