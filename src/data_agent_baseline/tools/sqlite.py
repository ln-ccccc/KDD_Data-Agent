from __future__ import annotations

import sqlite3
from pathlib import Path
from time import perf_counter


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def inspect_sqlite_schema(path: Path) -> dict[str, object]:
    """
    Inspect tables and columns in a sqlite/db file.
    Follows the 'Schema-first' constraint principle.
    """
    try:
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
            "status": "SUCCESS",
            "path": str(path),
            "tables": tables,
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "error_type": "FILE_ACCESS_ERROR",
            "error_message": str(e),
            "allowed_next_actions": ["list_context", "Check if the file is a valid SQLite database"]
        }


def execute_read_only_sql(
    path: Path,
    sql: str,
    *,
    limit: int = 200,
    time_limit_seconds: float = 2.5,
) -> dict[str, object]:
    """
    Execute a read-only SQL query against a SQLite database with structured output.
    Shifts from 'prompting' to 'constraint-based' design.
    """
    normalized_sql = sql.lstrip().lower()
    if not normalized_sql.startswith(("select", "with", "pragma")):
        return {
            "success": False,
            "status": "ERROR",
            "error_type": "SECURITY_ERROR",
            "error_message": "Only read-only SQL statements (SELECT, WITH, PRAGMA) are allowed.",
            "allowed_next_actions": ["Rewrite the query using SELECT"]
        }

    try:
        with _connect_read_only(path) as conn:
            started_at = perf_counter()

            def _progress_handler() -> int:
                if time_limit_seconds <= 0:
                    return 0
                if (perf_counter() - started_at) >= time_limit_seconds:
                    return 1  # non-zero aborts
                return 0

            conn.set_progress_handler(_progress_handler, 10_000)
            cursor = conn.execute(sql)
            
            if cursor.description is None:
                return {
                    "success": True,
                    "status": "SUCCESS",
                    "columns": [],
                    "rows": [],
                    "row_count": 0
                }

            column_names = [item[0] for item in cursor.description]
            rows = cursor.fetchmany(limit + 1)

        truncated = len(rows) > limit
        limited_rows = rows[:limit]
        
        result = {
            "success": True,
            "status": "SUCCESS",
            "path": str(path),
            "columns": column_names,
            "rows": [list(row) for row in limited_rows],
            "row_count": len(limited_rows),
            "truncated": truncated,
        }

        # 0-row handling (Zero Result Constraint)
        if len(limited_rows) == 0 and normalized_sql.startswith("select"):
            result["status"] = "ZERO_RESULT"
            result["allowed_next_actions"] = [
                "inspect_sqlite_schema",
                "execute_context_sql (using SELECT DISTINCT to verify categorical values)",
                "read_doc (check knowledge.md for business rules/status codes)"
            ]

        return result

    except sqlite3.Error as e:
        err_msg = str(e)
        error_type = "EXECUTION_ERROR"
        allowed_actions = ["inspect_sqlite_schema", "Verify table/column names"]

        if "no such table" in err_msg.lower() or "no such column" in err_msg.lower():
            error_type = "SCHEMA_ERROR"
            allowed_actions = ["inspect_sqlite_schema", "Check for typos or case-sensitivity"]
        elif "syntax error" in err_msg.lower():
            error_type = "SYNTAX_ERROR"
            allowed_actions = ["Check SQLite SQL syntax", "Verify quoting for identifiers"]
        elif "interrupted" in err_msg.lower() or "aborted" in err_msg.lower():
            error_type = "TIMEOUT_ERROR"
            allowed_actions = ["Simplify the query", "Add more filters", "Avoid cross-joins"]

        return {
            "success": False,
            "status": "ERROR",
            "error_type": error_type,
            "error_message": err_msg,
            "allowed_next_actions": allowed_actions
        }
    except Exception as e:
        return {
            "success": False,
            "status": "ERROR",
            "error_type": "SYSTEM_ERROR",
            "error_message": str(e),
            "allowed_next_actions": ["Retry with a simpler query"]
        }
