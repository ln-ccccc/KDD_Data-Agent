from __future__ import annotations

import os
import re
from typing import Any

from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.duckdb_runtime import connect, fetchone_limit1
from data_agent_baseline.tools.types import ToolExecutionResult


def _question_allows_empty(task: PublicTask) -> bool:
    question = getattr(task, "question", "") or ""
    q = question.lower()
    return any(token in q for token in (" no ", " none", " without", " zero", " 0 ", " empty", "没有", "无"))


def _extract_literal_pairs(sql: str) -> list[tuple[str, str]]:
    literal_pairs: list[tuple[str, str]] = []
    literal_pairs.extend(re.findall(r"(?is)\b([a-zA-Z_][\w\.]*?)\s*=\s*'([^']*)'", sql))
    for col, raw_list in re.findall(r"(?is)\b([a-zA-Z_][\w\.]*?)\s+IN\s*\(([^)]*)\)", sql):
        items = [item.strip() for item in raw_list.split(",")]
        for item in items:
            if len(item) >= 2 and item[0] == "'" and item[-1] == "'":
                literal_pairs.append((col, item[1:-1].replace("''", "'")))
    return literal_pairs[:16]


def _extract_tables(sql: str) -> list[str]:
    table_tokens = re.findall(r"(?is)\b(from|join)\s+('(?:[^']|''?)*')", sql)
    return [t[1] for t in table_tokens][:8]


def _suspicious_empty_result_hint(*, missing_literals: list[tuple[str, str]]) -> str:
    hint_bits: list[str] = []
    hint_bits.append(
        "Your query returned 0 rows. This is often caused by an incorrect categorical literal, wrong column choice, or overly strict filters."
    )
    if missing_literals:
        rendered = ", ".join(f"{c}='{v}'" for c, v in missing_literals[:6])
        hint_bits.append(f"Some literals may not exist in the data: {rendered}.")
    hint_bits.append("Validate categorical values with SELECT DISTINCT or a LIMIT 5 preview before final submission.")
    hint_bits.append("Do not claim the result is empty unless the question explicitly allows an empty result.")
    return " ".join(hint_bits)


def handle_answer(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    final_sql = action_input.get("final_sql")
    file_path = action_input.get("file_path")
    allow_empty_result = bool(action_input.get("allow_empty_result", False))
    columns = action_input.get("columns")
    rows = action_input.get("rows")

    allows_empty = _question_allows_empty(task)

    if final_sql is not None:
        final_sql_str = str(final_sql).strip().rstrip(";")
        try:
            conn = connect(task.context_dir)
            try:
                first_row = fetchone_limit1(conn, final_sql_str)
                if first_row is None and not allows_empty:
                    tables = _extract_tables(final_sql_str)
                    literal_pairs = _extract_literal_pairs(final_sql_str)
                    if literal_pairs:
                        missing_literals: list[tuple[str, str]] = []
                        for col, literal in literal_pairs:
                            col_name = col.split(".")[-1]
                            literal_escaped = literal.replace("'", "''")
                            found = False
                            for tbl in tables:
                                try:
                                    probe = conn.execute(
                                        f"SELECT 1 FROM {tbl} WHERE {col_name} = '{literal_escaped}' LIMIT 1"
                                    ).fetchone()
                                    if probe is not None:
                                        found = True
                                        break
                                except Exception:
                                    continue
                            if not found and tables:
                                missing_literals.append((col_name, literal))
                        return ToolExecutionResult(
                            ok=False,
                            content={
                                "error": {"type": "SuspiciousEmptyResult", "message": "final_sql returned 0 rows."},
                                "hint": _suspicious_empty_result_hint(missing_literals=missing_literals),
                            },
                            is_terminal=False,
                        )
            finally:
                conn.close()
        except Exception as e:
            return ToolExecutionResult(
                ok=False,
                content={
                    "error": {
                        "type": "InvalidSQL",
                        "message": f"Your final_sql is invalid and failed to execute: {e}",
                    },
                    "hint": "Fix your SQL syntax, table/column names, or file paths.",
                },
                is_terminal=False,
            )

        answer = AnswerTable(final_sql=final_sql_str, allow_empty_result=allow_empty_result)
        return ToolExecutionResult(
            ok=True,
            content={"status": "submitted", "final_sql": final_sql_str},
            is_terminal=True,
            answer=answer,
        )

    if file_path is not None:
        resolved = str(file_path)
        actual_path = resolved
        if not os.path.isabs(resolved):
            actual_path = str(task.context_dir / resolved)
        if not os.path.exists(actual_path):
            return ToolExecutionResult(
                ok=False,
                content={
                    "error": {"type": "FileNotFound", "message": f"The file_path you provided does not exist: {resolved}"},
                    "hint": "Save your dataframe to a CSV file first (df.to_csv(...)) before submitting file_path.",
                },
                is_terminal=False,
            )

        answer = AnswerTable(file_path=resolved, allow_empty_result=allow_empty_result)
        return ToolExecutionResult(
            ok=True,
            content={"status": "submitted", "file_path": resolved},
            is_terminal=True,
            answer=answer,
        )

    if isinstance(rows, list) and len(rows) == 0 and not allows_empty:
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {
                    "type": "SuspiciousEmptyResult",
                    "message": "You are submitting an empty table. This is likely caused by an incorrect categorical literal or an overly strict filter.",
                },
                "hint": "Verify categorical values with SELECT DISTINCT or a LIMIT 5 preview. Do not submit an empty table unless the question explicitly allows an empty result.",
            },
            is_terminal=False,
        )

    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) for item in columns):
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {"type": "InvalidAnswer", "message": "answer.columns must be a non-empty list of strings."},
                "hint": "Example: {\"columns\": [\"col1\"], \"rows\": [[\"value1\"]]}",
            },
            is_terminal=False,
        )
    if not isinstance(rows, list):
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {"type": "InvalidAnswer", "message": "answer.rows must be a list."},
                "hint": "rows must be a list of rows; each row must be a list with same length as columns.",
            },
            is_terminal=False,
        )

    normalized_rows: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, list):
            return ToolExecutionResult(
                ok=False,
                content={
                    "error": {"type": "InvalidAnswer", "message": "Each answer row must be a list."},
                    "hint": "rows should look like: [[val1, val2], [val1, val2]]",
                },
                is_terminal=False,
            )
        if len(row) != len(columns):
            return ToolExecutionResult(
                ok=False,
                content={
                    "error": {
                        "type": "InvalidAnswer",
                        "message": "Each answer row must match the number of columns.",
                        "expected_columns": len(columns),
                        "got_columns": len(row),
                    },
                    "hint": "Ensure every row length equals len(columns).",
                },
                is_terminal=False,
            )
        normalized_rows.append(list(row))

    answer = AnswerTable(columns=list(columns), rows=normalized_rows, allow_empty_result=allow_empty_result)
    return ToolExecutionResult(
        ok=True,
        content={"status": "submitted", "column_count": len(columns), "row_count": len(normalized_rows)},
        is_terminal=True,
        answer=answer,
    )
