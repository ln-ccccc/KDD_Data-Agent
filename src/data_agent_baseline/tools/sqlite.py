from __future__ import annotations

import sqlite3
from pathlib import Path
from time import perf_counter


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def inspect_sqlite_schema(path: Path) -> dict[str, object]:
    with _connect_read_only(path) as conn:
        rows = conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        tables: list[dict[str, object]] = []
        for name, create_sql in rows:
            tables.append(
                {
                    "name": name,
                    "create_sql": create_sql,
                }
            )
    return {
        "path": str(path),
        "tables": tables,
    }


def execute_read_only_sql(
    path: Path,
    sql: str,
    *,
    limit: int = 200,
    time_limit_seconds: float = 2.5,
) -> dict[str, object]:
    normalized_sql = sql.lstrip().lower()
    if not normalized_sql.startswith(("select", "with", "pragma")):
        raise ValueError("Only read-only SQL statements are allowed.")

    with _connect_read_only(path) as conn:
        # Soft time limit to prevent a single query from stalling the whole agent.
        started_at = perf_counter()

        def _progress_handler() -> int:
            if time_limit_seconds <= 0:
                return 0
            if (perf_counter() - started_at) >= time_limit_seconds:
                return 1  # non-zero aborts
            return 0

        # Call handler every N SQLite virtual machine instructions.
        conn.set_progress_handler(_progress_handler, 10_000)
        cursor = conn.execute(sql)
        column_names = [item[0] for item in cursor.description or []]
        rows = cursor.fetchmany(limit + 1)

    truncated = len(rows) > limit
    limited_rows = rows[:limit]
    return {
        "path": str(path),
        "columns": column_names,
        "rows": [list(row) for row in limited_rows],
        "row_count": len(limited_rows),
        "truncated": truncated,
    }
