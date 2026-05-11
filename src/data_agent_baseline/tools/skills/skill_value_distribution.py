import re
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.duckdb_exec import execute_duckdb_sql
from data_agent_baseline.tools.filesystem import resolve_context_path
from data_agent_baseline.tools.registry import ToolExecutionResult, ToolSpec
from data_agent_baseline.tools.sqlite import execute_read_only_sql


TOOL_SPEC = ToolSpec(
    name="skill_value_distribution",
    description=(
        "Skill: Profile one column's value distribution (null ratio, distinct count, top values, min/max). "
        "Use when filter/join values are uncertain."
    ),
    input_schema={
        "engine": "duckdb | sqlite",
        "table": "Table or relation expression, e.g. 'sales.csv' (duckdb) or sales (sqlite)",
        "column": "Column name to profile.",
        "db_path": "Required for sqlite engine.",
        "top_k": "Optional top value count, default 10.",
    },
)


def _safe_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _safe_table_expr(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        raise ValueError("table is required.")
    if stripped.startswith("'") and stripped.endswith("'"):
        return stripped
    if re.search(r"\s", stripped):
        return stripped
    if "." in stripped:
        return ".".join(_safe_identifier(part) for part in stripped.split("."))
    return _safe_identifier(stripped)


def _duckdb_profile(task: PublicTask, table: str, column: str, top_k: int) -> dict[str, Any]:
    table_expr = _safe_table_expr(table)
    col_expr = _safe_identifier(column)

    overview_sql = (
        f"SELECT COUNT(*) AS total_rows, "
        f"SUM(CASE WHEN {col_expr} IS NULL THEN 1 ELSE 0 END) AS null_count, "
        f"COUNT(DISTINCT {col_expr}) AS distinct_count, "
        f"MIN({col_expr}) AS min_value, MAX({col_expr}) AS max_value "
        f"FROM {table_expr}"
    )
    top_sql = (
        f"SELECT {col_expr} AS value, COUNT(*) AS count "
        f"FROM {table_expr} GROUP BY {col_expr} ORDER BY count DESC LIMIT {top_k}"
    )
    overview = execute_duckdb_sql(task.context_dir, overview_sql)
    tops = execute_duckdb_sql(task.context_dir, top_sql)
    if not overview.get("success"):
        return {"error": overview.get("error", "failed to profile column")}
    row = (overview.get("rows") or [[0, 0, 0, None, None]])[0]
    total = int(row[0] or 0)
    null_count = int(row[1] or 0)
    return {
        "null_ratio": (null_count / total) if total else 0.0,
        "distinct_count": int(row[2] or 0),
        "top_values": [
            {"value": item[0], "count": item[1]} for item in (tops.get("rows") or [])
        ],
        "min": row[3],
        "max": row[4],
    }


def _sqlite_profile(task: PublicTask, db_path: str, table: str, column: str, top_k: int) -> dict[str, Any]:
    db = resolve_context_path(task, db_path)
    table_expr = _safe_identifier(table)
    col_expr = _safe_identifier(column)
    overview_sql = (
        f"SELECT COUNT(*) AS total_rows, "
        f"SUM(CASE WHEN {col_expr} IS NULL THEN 1 ELSE 0 END) AS null_count, "
        f"COUNT(DISTINCT {col_expr}) AS distinct_count, "
        f"MIN({col_expr}) AS min_value, MAX({col_expr}) AS max_value "
        f"FROM {table_expr}"
    )
    top_sql = (
        f"SELECT {col_expr} AS value, COUNT(*) AS count "
        f"FROM {table_expr} GROUP BY {col_expr} ORDER BY count DESC LIMIT {top_k}"
    )
    overview = execute_read_only_sql(db, overview_sql)
    tops = execute_read_only_sql(db, top_sql)
    if not overview.get("success"):
        return {"error": overview.get("error", "failed to profile column")}
    row = (overview.get("rows") or [[0, 0, 0, None, None]])[0]
    total = int(row[0] or 0)
    null_count = int(row[1] or 0)
    return {
        "null_ratio": (null_count / total) if total else 0.0,
        "distinct_count": int(row[2] or 0),
        "top_values": [
            {"value": item[0], "count": item[1]} for item in (tops.get("rows") or [])
        ],
        "min": row[3],
        "max": row[4],
    }


def TOOL_HANDLER(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    try:
        engine = str(action_input.get("engine", "duckdb")).lower()
        table = str(action_input.get("table", "")).strip()
        column = str(action_input.get("column", "")).strip()
        top_k = int(action_input.get("top_k", 10))
        if not table or not column:
            return ToolExecutionResult(
                ok=False, content={"error": "table and column are required."}
            )
        if engine == "sqlite":
            db_path = str(action_input.get("db_path", "")).strip()
            if not db_path:
                return ToolExecutionResult(
                    ok=False, content={"error": "db_path is required for sqlite profiling."}
                )
            profile = _sqlite_profile(task, db_path, table, column, top_k)
        else:
            profile = _duckdb_profile(task, table, column, top_k)
        return ToolExecutionResult(ok="error" not in profile, content=profile)
    except Exception as exc:  # noqa: BLE001
        return ToolExecutionResult(ok=False, content={"error": str(exc)})
