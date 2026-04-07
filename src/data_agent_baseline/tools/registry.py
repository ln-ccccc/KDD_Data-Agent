from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.filesystem import (
    list_context_tree,
    read_csv_preview,
    read_doc_preview,
    read_json_preview,
    resolve_context_path,
)
from data_agent_baseline.tools.python_exec import execute_python_code
from data_agent_baseline.tools.sqlite import execute_read_only_sql, inspect_sqlite_schema
from data_agent_baseline.tools.duckdb_exec import execute_duckdb_sql

EXECUTE_PYTHON_TIMEOUT_SECONDS = 100
MAX_EXECUTE_PYTHON_CODE_CHARS = 12000
BLOCKED_EXECUTE_PYTHON_PATTERNS = (
    "subprocess",
    "os.system",
    "pty.spawn",
    "shutil.rmtree",
    "rm -rf",
    "curl ",
    "wget ",
)

# Tool schema definition
@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

# Tool execution result definition（observation）
@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    ok: bool
    content: dict[str, Any]
    is_terminal: bool = False
    answer: AnswerTable | None = None

# 作用是定义工具的执行函数，接收任务、输入和输出的类型。
ToolHandler = Callable[[PublicTask, dict[str, Any]], ToolExecutionResult]


def _validate_tool_action_input(action: str, action_input: dict[str, Any]) -> None:
    if action != "execute_python":
        return
    code = str(action_input.get("code", ""))
    if not code.strip():
        raise ValueError("execute_python.code must not be empty.")
    if len(code) > MAX_EXECUTE_PYTHON_CODE_CHARS:
        raise ValueError(
            "execute_python.code is too long and has been blocked by policy."
        )
    lowered = code.lower()
    for pattern in BLOCKED_EXECUTE_PYTHON_PATTERNS:
        if pattern in lowered:
            raise ValueError(
                f"execute_python blocked by policy due to disallowed pattern: {pattern}"
            )


# 文本工具：列出上下文目录树，用于获取任务上下文的目录结构
def _list_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    max_depth = int(action_input.get("max_depth", 4))
    return ToolExecutionResult(ok=True, content=list_context_tree(task, max_depth=max_depth))

# 文本工具：读取CSV文件预览，用于快速查看CSV文件的内容
def _read_csv(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_rows = int(action_input.get("max_rows", 20))
    res = read_csv_preview(task, path, max_rows=max_rows)
    res["cheat_sheet"] = _get_cheat_sheet("csv")
    return ToolExecutionResult(ok=True, content=res)

def _get_cheat_sheet(topic: str) -> str:
    cheat_sheet_dir = Path(__file__).resolve().parents[4] / "artifacts" / "cheat_sheets"
    file_path = cheat_sheet_dir / f"{topic}_hints.md"
    if file_path.exists():
        try:
            with open(file_path, "r") as f:
                return f"\n\n[YOUR PREVIOUS LEARNINGS ON {topic.upper()}]\n" + f.read()
        except Exception:
            pass
    return ""

# 文本工具：读取JSON文件预览，用于快速查看JSON文件的内容
def _read_json(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    res = read_json_preview(task, path, max_chars=max_chars)
    res["cheat_sheet"] = _get_cheat_sheet("json")
    return ToolExecutionResult(ok=True, content=res)

# 文本工具：读取DOCX文件预览，用于快速查看DOCX文件的内容
def _read_doc(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    return ToolExecutionResult(ok=True, content=read_doc_preview(task, path, max_chars=max_chars))


def _inspect_sqlite_schema(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    res = inspect_sqlite_schema(path)
    res["cheat_sheet"] = _get_cheat_sheet("sqlite")
    return ToolExecutionResult(ok=True, content=res)


def _execute_context_sql(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    sql = str(action_input["sql"])
    limit = int(action_input.get("limit", 200))
    return ToolExecutionResult(ok=True, content=execute_read_only_sql(path, sql, limit=limit))


def _execute_duckdb_sql(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    sql = str(action_input["sql"])
    limit = int(action_input.get("limit", 200))
    content = execute_duckdb_sql(task.context_dir, sql, limit=limit)
    return ToolExecutionResult(ok=bool(content.get("success")), content=content)


def _execute_python(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    code = str(action_input["code"])
    content = execute_python_code(
        context_root=task.context_dir,
        code=code,
        timeout_seconds=EXECUTE_PYTHON_TIMEOUT_SECONDS,
    )
    return ToolExecutionResult(ok=bool(content.get("success")), content=content)


def _update_cheat_sheet(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    topic = str(action_input.get("topic", "general"))
    error_pattern = str(action_input.get("error_pattern", ""))
    solution = str(action_input.get("solution", ""))
    reason = str(action_input.get("reason", ""))

    cheat_sheet_dir = Path(__file__).resolve().parents[4] / "artifacts" / "cheat_sheets"
    cheat_sheet_dir.mkdir(parents=True, exist_ok=True)
    file_path = cheat_sheet_dir / f"{topic}_hints.md"

    entry = f"\n\n### Learned from Task {task.task_id}\n"
    entry += f"- **Error Pattern:** {error_pattern}\n"
    entry += f"- **Solution:** {solution}\n"
    entry += f"- **Reasoning:** {reason}\n"

    with open(file_path, "a") as f:
        f.write(entry)

    return ToolExecutionResult(ok=True, content={"success": True, "message": f"Cheat sheet for '{topic}' updated successfully."})


def _answer(_: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    columns = action_input.get("columns")
    rows = action_input.get("rows")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) for item in columns):
        raise ValueError("answer.columns must be a non-empty list of strings.")
    if not isinstance(rows, list):
        raise ValueError("answer.rows must be a list.")

    normalized_rows: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("Each answer row must be a list.")
        if len(row) != len(columns):
            raise ValueError("Each answer row must match the number of columns.")
        normalized_rows.append(list(row))

    answer = AnswerTable(columns=list(columns), rows=normalized_rows)
    return ToolExecutionResult(
        ok=True,
        content={
            "status": "submitted",
            "column_count": len(columns),
            "row_count": len(normalized_rows),
        },
        is_terminal=True,
        answer=answer,
    )


@dataclass(slots=True)
class ToolRegistry:
    specs: dict[str, ToolSpec]
    handlers: dict[str, ToolHandler]

    def describe_for_prompt(self) -> str:
        lines = []
        for name in sorted(self.specs):
            spec = self.specs[name]
            lines.append(f"- {spec.name}: {spec.description}")
            lines.append(f"  input_schema: {spec.input_schema}")
        return "\n".join(lines)

    def execute(self, task: PublicTask, action: str, action_input: dict[str, Any]) -> ToolExecutionResult:
        if action not in self.handlers:
            raise KeyError(f"Unknown tool: {action}")
        _validate_tool_action_input(action, action_input)
        return self.handlers[action](task, action_input)


def create_default_tool_registry() -> ToolRegistry:
    specs = {
        "answer": ToolSpec(
            name="answer",
            description="Submit the final answer table. This is the only valid terminating action.",
            input_schema={
                "columns": ["column_name"],
                "rows": [["value_1"]],
            },
        ),
        "execute_duckdb_sql": ToolSpec(
            name="execute_duckdb_sql",
            description="Run a SQL query via DuckDB in the context directory. Can query CSV, Parquet, and JSON files directly (e.g., SELECT * FROM 'data.csv'). IMPORTANT: To query nested arrays in JSON (like {'records': [...]}), use `read_json_auto('file.json')` and UNNEST, or `SELECT * FROM (SELECT unnest(records) as r FROM read_json_auto('file.json'))`.",
            input_schema={"sql": "SELECT * FROM 'data.csv'", "limit": 200},
        ),
        "execute_context_sql": ToolSpec(
            name="execute_context_sql",
            description="Run a read-only SQL query against a sqlite/db file inside context.",
            input_schema={"path": "relative/path/to/file.sqlite", "sql": "SELECT ...", "limit": 200},
        ),
        "execute_python": ToolSpec(
            name="execute_python",
            description=(
                "Execute arbitrary Python code with the task context directory as the "
                "working directory. The tool returns the code's captured stdout as `output`. "
                f"The execution timeout is fixed at {EXECUTE_PYTHON_TIMEOUT_SECONDS} seconds."
            ),
            input_schema={
                "code": "import os\nprint(sorted(os.listdir('.')))",
            },
        ),
        "inspect_sqlite_schema": ToolSpec(
            name="inspect_sqlite_schema",
            description="Inspect tables and columns in a sqlite/db file inside context.",
            input_schema={"path": "relative/path/to/file.sqlite"},
        ),
        "list_context": ToolSpec(
            name="list_context",
            description="List files and directories available under context.",
            input_schema={"max_depth": 4},
        ),
        "read_csv": ToolSpec(
            name="read_csv",
            description="Read a preview of a CSV file inside context.",
            input_schema={"path": "relative/path/to/file.csv", "max_rows": 20},
        ),
        "read_doc": ToolSpec(
            name="read_doc",
            description="Read a text-like document inside context.",
            input_schema={"path": "relative/path/to/file.md", "max_chars": 4000},
        ),
        "read_json": ToolSpec(
            name="read_json",
            description="Read a preview of a JSON file inside context.",
            input_schema={"path": "relative/path/to/file.json", "max_chars": 4000},
        ),
    "update_cheat_sheet": ToolSpec(
            name="update_cheat_sheet",
            description="Record a successful solution to a previously encountered error. Use this to help yourself in future tasks.",
            input_schema={
                "topic": "e.g., 'json', 'csv', 'duckdb', 'sqlite'",
                "error_pattern": "The error message you encountered",
                "solution": "The exact code/SQL that fixed it",
                "reason": "Why the solution worked"
            },
        ),
    }

    handlers = {
        "answer": _answer,
        "update_cheat_sheet": _update_cheat_sheet,
        "execute_duckdb_sql": _execute_duckdb_sql,
        "execute_context_sql": _execute_context_sql,
        "execute_python": _execute_python,
        "inspect_sqlite_schema": _inspect_sqlite_schema,
        "list_context": _list_context,
        "read_csv": _read_csv,
        "read_doc": _read_doc,
        "read_json": _read_json,
    }
    return ToolRegistry(specs=specs, handlers=handlers)
