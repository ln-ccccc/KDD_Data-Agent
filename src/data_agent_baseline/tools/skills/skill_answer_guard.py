from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolExecutionResult, ToolSpec


TOOL_SPEC = ToolSpec(
    name="skill_answer_guard",
    description=(
        "Lightweight answer guard with deterministic checks only: empty rows, all-null columns, all-zero numeric columns."
    ),
    input_schema={
        "columns": ["column_name"],
        "rows": [["value_1"]],
    },
)


def TOOL_HANDLER(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    del task
    columns = action_input.get("columns")
    rows = action_input.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return ToolExecutionResult(
            ok=False,
            content={"error": "columns and rows are required and must be lists."},
        )
    all_null_columns: list[str] = []
    all_zero_numeric_columns: list[str] = []
    for idx, col in enumerate(columns):
        col_values = []
        for row in rows:
            if isinstance(row, list) and idx < len(row):
                col_values.append(row[idx])
            else:
                col_values.append(None)
        if col_values and all(v is None for v in col_values):
            all_null_columns.append(str(col))
        numeric_vals = [v for v in col_values if isinstance(v, (int, float))]
        if numeric_vals and all(float(v) == 0.0 for v in numeric_vals):
            all_zero_numeric_columns.append(str(col))

    result = {
        "is_empty": len(rows) == 0,
        "all_null_columns": all_null_columns,
        "all_zero_agg": len(all_zero_numeric_columns) > 0,
        "all_zero_columns": all_zero_numeric_columns,
    }
    return ToolExecutionResult(ok=True, content=result)
