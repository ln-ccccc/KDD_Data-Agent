from typing import Any, Dict, List

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolExecutionResult, ToolSpec


TOOL_SPEC = ToolSpec(
    name="skill_answer_guard",
    description=(
        "Enhanced answer guard with deterministic checks: "
        "empty rows, all-null columns, all-zero numeric columns, "
        "AND filter column visibility validation."
    ),
    input_schema={
        "columns": ["column_name"],
        "rows": [["value_1"]],
        # 可选字段（由 agent 在调用时传入）
        "required_columns": ["col_a"],   # 必须出现在输出中的列（来自 WHERE / JOIN）
    },
)


def TOOL_HANDLER(task: PublicTask, action_input: Dict[str, Any]) -> ToolExecutionResult:
    del task

    columns = action_input.get("columns")
    rows = action_input.get("rows")
    required_columns = action_input.get("required_columns", [])

    # -----------------------------
    # 基础校验
    # -----------------------------
    if not isinstance(columns, list) or not isinstance(rows, list):
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {
                    "type": "INVALID_ANSWER_FORMAT",
                    "message": "columns and rows must be lists.",
                }
            },
        )

    # -----------------------------
    # 1. 空结果检查
    # -----------------------------
    if len(rows) == 0:
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {
                    "type": "EMPTY_RESULT",
                    "message": "Empty result set.",
                }
            },
        )

    # -----------------------------
    # 2. 列级检查
    # -----------------------------
    all_null_columns: List[str] = []
    all_zero_numeric_columns: List[str] = []

    for idx, col in enumerate(columns):
        col_values = []
        for row in rows:
            if isinstance(row, list) and idx < len(row):
                col_values.append(row[idx])
            else:
                col_values.append(None)

        # 全 NULL 列
        if col_values and all(v is None for v in col_values):
            all_null_columns.append(str(col))

        # 全 0 数值列
        numeric_vals = [v for v in col_values if isinstance(v, (int, float))]
        if numeric_vals and all(float(v) == 0.0 for v in numeric_vals):
            all_zero_numeric_columns.append(str(col))

    # -----------------------------
    # 3. 核心：Filter 可验证性检查
    # -----------------------------
    missing_required = [
        col for col in required_columns if col not in columns
    ]

    if missing_required:
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {
                    "type": "MISSING_REQUIRED_COLUMNS",
                    "message": "Missing required columns for verification.",
                    "missing_columns": missing_required,
                },
                "hint": "Columns used in WHERE/GROUP BY/JOIN should appear in output.",
            },
        )

    if all_null_columns:
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {
                    "type": "ALL_NULL_COLUMN",
                    "message": "One or more columns are entirely NULL.",
                    "columns": all_null_columns,
                },
                "hint": "Check JOIN/filter logic and selected fields.",
            },
        )

    if all_zero_numeric_columns:
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {
                    "type": "ALL_ZERO_RESULT",
                    "message": "One or more numeric columns are all zero.",
                    "columns": all_zero_numeric_columns,
                },
                "hint": "Check aggregation/filter conditions.",
            },
        )

    result = {
        "is_empty": False,
        "all_null_columns": all_null_columns,
        "all_zero_agg": len(all_zero_numeric_columns) > 0,
        "all_zero_columns": all_zero_numeric_columns,
        "verification_passed": True,
    }

    return ToolExecutionResult(ok=True, content=result)
