from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from data_agent_baseline.tools.duckdb_exec import DUCKDB_EXT_DIR


def connect(context_dir: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(
        ":memory:",
        config={
            "extension_directory": DUCKDB_EXT_DIR.as_posix(),
            "home_directory": context_dir.resolve().as_posix(),
        },
    )
    try:
        conn.execute("LOAD json;")
    except Exception:
        pass
    try:
        conn.execute("LOAD sqlite;")
        conn.execute("LOAD excel;")
    except Exception:
        pass
    resolved_context = context_dir.resolve().as_posix()
    conn.execute(f"SET file_search_path = '{resolved_context}'")
    return conn


def fetchone_limit1(conn: duckdb.DuckDBPyConnection, sql: str) -> Any | None:
    sql_str = sql.strip().rstrip(";")
    cursor = conn.execute(f"SELECT * FROM ({sql_str}) LIMIT 1")
    return cursor.fetchone()


def export_query_to_csv(*, context_dir: Path, sql: str, output_csv_path: Path) -> None:
    conn = connect(context_dir)
    try:
        out_path = output_csv_path.resolve().as_posix().replace("'", "''")
        sql_str = sql.strip().rstrip(";")
        conn.execute(f"COPY ({sql_str}) TO '{out_path}' (HEADER, DELIMITER ',');")
    finally:
        conn.close()
