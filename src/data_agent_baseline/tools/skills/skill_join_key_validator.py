import re
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.duckdb_exec import execute_duckdb_sql
from data_agent_baseline.tools.filesystem import resolve_context_path
from data_agent_baseline.tools.registry import ToolExecutionResult, ToolSpec
from data_agent_baseline.tools.sqlite import execute_read_only_sql


TOOL_SPEC = ToolSpec(
    name="skill_join_key_validator",
    description=(
        "Skill: Validate join-key cardinality and join explosion risk. "
        "Must be triggered when SQL contains JOIN."
    ),
    input_schema={
        "sql": "SQL text that contains at least one JOIN.",
        "engine": "duckdb | sqlite",
        "db_path": "Required for sqlite engine.",
    },
)


JOIN_PATTERN = re.compile(
    r"from\s+(?P<left_table>[^\s]+)(?:\s+(?:as\s+)?(?P<left_alias>\w+))?"
    r"\s+join\s+(?P<right_table>[^\s]+)(?:\s+(?:as\s+)?(?P<right_alias>\w+))?"
    r"\s+on\s+(?P<left_on>[\w\.\"`]+)\s*=\s*(?P<right_on>[\w\.\"`]+)",
    flags=re.IGNORECASE,
)


def _extract_last_identifier(expr: str) -> str:
    part = expr.strip().replace("`", "").replace('"', "")
    return part.split(".")[-1]


def _run(engine: str, task: PublicTask, db_path: str, sql: str) -> dict[str, Any]:
    match = JOIN_PATTERN.search(sql)
    if not match:
        return {
            "join_detected": True,
            "join_type_inferred": "unknown",
            "join_risk": "UNKNOWN",
            "recommendation": "JOIN detected but parser could not extract keys. Validate manually.",
        }

    left_table = match.group("left_table")
    right_table = match.group("right_table")
    left_on = match.group("left_on")
    right_on = match.group("right_on")
    left_key = _extract_last_identifier(left_on)
    right_key = _extract_last_identifier(right_on)

    if engine == "sqlite":
        db = resolve_context_path(task, db_path)

        def execute(query: str) -> dict[str, Any]:
            return execute_read_only_sql(db, query)
    else:
        def execute(query: str) -> dict[str, Any]:
            return execute_duckdb_sql(task.context_dir, query)

    left_stats = execute(
        f"SELECT COUNT(*) AS total_rows, COUNT(DISTINCT {_extract_last_identifier(left_on)}) AS distinct_rows FROM {left_table}"
    )
    right_stats = execute(
        f"SELECT COUNT(*) AS total_rows, COUNT(DISTINCT {_extract_last_identifier(right_on)}) AS distinct_rows FROM {right_table}"
    )
    if not left_stats.get("success") or not right_stats.get("success"):
        return {
            "join_detected": True,
            "join_type_inferred": "unknown",
            "join_risk": "UNKNOWN",
            "recommendation": "Could not compute key cardinality; inspect schema and keys manually.",
            "details": {
                "left_error": left_stats.get("error"),
                "right_error": right_stats.get("error"),
            },
        }

    left_total, left_distinct = left_stats["rows"][0]
    right_total, right_distinct = right_stats["rows"][0]
    left_unique = int(left_total or 0) == int(left_distinct or 0)
    right_unique = int(right_total or 0) == int(right_distinct or 0)

    if left_unique and right_unique:
        inferred = "one_to_one"
        risk = "LOW"
        recommendation = "Safe join cardinality."
    elif left_unique and not right_unique:
        inferred = "one_to_many"
        risk = "HIGH"
        recommendation = "Potential row multiplication. Consider GROUP BY on right side before JOIN."
    elif not left_unique and right_unique:
        inferred = "many_to_one"
        risk = "MEDIUM"
        recommendation = "Validate whether many-to-one fan-in is intended."
    else:
        inferred = "many_to_many"
        risk = "HIGH"
        recommendation = "High join explosion risk. Pre-aggregate both sides before JOIN."

    return {
        "join_detected": True,
        "join_type_inferred": inferred,
        "join_risk": risk,
        "recommendation": recommendation,
        "keys": {"left_key": left_key, "right_key": right_key},
        "cardinality": {
            "left_total": left_total,
            "left_distinct_key": left_distinct,
            "right_total": right_total,
            "right_distinct_key": right_distinct,
        },
    }


def TOOL_HANDLER(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    try:
        sql = str(action_input.get("sql", "")).strip()
        if not sql:
            return ToolExecutionResult(ok=False, content={"error": "sql is required."})
        if not re.search(r"\bjoin\b", sql, flags=re.IGNORECASE):
            return ToolExecutionResult(
                ok=True,
                content={"join_detected": False, "message": "No JOIN found; validator skipped."},
            )
        engine = str(action_input.get("engine", "duckdb")).lower()
        db_path = str(action_input.get("db_path", "")).strip()
        if engine == "sqlite" and not db_path:
            return ToolExecutionResult(
                ok=False, content={"error": "db_path is required for sqlite engine."}
            )
        result = _run(engine=engine, task=task, db_path=db_path, sql=sql)
        return ToolExecutionResult(ok=True, content=result)
    except Exception as exc:  # noqa: BLE001
        return ToolExecutionResult(ok=False, content={"error": str(exc)})
