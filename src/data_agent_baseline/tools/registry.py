from __future__ import annotations

import json
import re
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
    get_data_schema,
)
from data_agent_baseline.tools.python_exec import execute_python_code
from data_agent_baseline.tools.sqlite import execute_read_only_sql, inspect_sqlite_schema
from data_agent_baseline.tools.duckdb_exec import execute_duckdb_sql
from data_agent_baseline.tools.eda import extract_dataframe_stats
from data_agent_baseline.tools.knowledge_retriever import search_knowledge_doc, list_knowledge_headings, read_knowledge_section
from data_agent_baseline.tools.schema_retriever import retrieve_schema_context
from data_agent_baseline.tools.subagent import build_subagent_tool, subagent_finish

EXECUTE_PYTHON_TIMEOUT_SECONDS = 100

# Tool schema definition
@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

# Tool execution result definition（observation）
# Tool execution result definition（observation）
@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    ok: bool
    content: dict[str, Any]
    is_terminal: bool = False
    answer: AnswerTable | None = None

# 作用是定义工具的执行函数，接收任务、输入和输出的类型。
# 作用是定义工具的执行函数，接收任务、输入和输出的类型。
ToolHandler = Callable[[PublicTask, dict[str, Any]], ToolExecutionResult]

# 文本工具：列出上下文目录树，用于获取任务上下文的目录结构
def _list_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    max_depth = int(action_input.get("max_depth", 4))
    return ToolExecutionResult(ok=True, content=list_context_tree(task, max_depth=max_depth))

# 文本工具：读取CSV文件预览，用于快速查看CSV文件的内容
def _read_csv(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_rows = int(action_input.get("max_rows", 20))
    return ToolExecutionResult(ok=True, content=read_csv_preview(task, path, max_rows=max_rows))

# 文本工具：读取JSON文件预览，用于快速查看JSON文件的内容
def _read_json(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    res = read_json_preview(task, path, max_chars=max_chars)
    res["cheat_sheet"] = _get_cheat_sheet("json")
    return ToolExecutionResult(ok=True, content=res)

# 文本工具：读取DOCX文件预览，用于快速查看DOCX文件的内容
# 文本工具：读取DOCX文件预览，用于快速查看DOCX文件的内容
def _read_doc(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    return ToolExecutionResult(ok=True, content=read_doc_preview(task, path, max_chars=max_chars))


def _get_data_schema(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    res = get_data_schema(task, path)
    return ToolExecutionResult(ok=True, content=res)


def _extract_dataframe_stats(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    res = extract_dataframe_stats(task, path)
    return ToolExecutionResult(ok=res.get("success", False), content=res)


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


def _get_cheat_sheet_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "artifacts" / "cheat_sheets"

def _update_cheat_sheet(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    topic = str(action_input.get("topic", "general"))
    error_pattern = str(action_input.get("error_pattern", ""))
    solution = str(action_input.get("solution", ""))
    reason = str(action_input.get("reason", ""))

    cheat_sheet_dir = _get_cheat_sheet_dir()
    cheat_sheet_dir.mkdir(parents=True, exist_ok=True)
    file_path = cheat_sheet_dir / f"{topic}_hints.md"

    entry = f"\n\n### Learned from Task {task.task_id}\n"
    entry += f"- **Error Pattern:** {error_pattern}\n"
    entry += f"- **Solution:** {solution}\n"
    entry += f"- **Reasoning:** {reason}\n"

    with open(file_path, "a", encoding="utf-8") as f:
        f.write(entry)

    return ToolExecutionResult(ok=True, content={"success": True, "message": f"Cheat sheet for '{topic}' updated successfully."})


def _answer(_: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    columns = action_input.get("columns")
    rows = action_input.get("rows")
    # Answer pre-check (do NOT raise): return a structured error so the agent can self-correct.
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) for item in columns):
        return ToolExecutionResult(
            ok=False,
            content={
                "error": {
                    "type": "InvalidAnswer",
                    "message": "answer.columns must be a non-empty list of strings.",
                },
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
        """描述所有工具"""
        lines = []
        # 排序工具名称，确保一致的输出顺序
        for name in sorted(self.specs):
            spec = self.specs[name]
            lines.append(f"- {spec.name}: {spec.description}")
            lines.append(f"  input_schema: {spec.input_schema}")
        return "\n".join(lines)

    def _run_sql_guard_skills(
        self,
        task: PublicTask,
        action: str,
        action_input: dict[str, Any],
    ) -> dict[str, Any]:
        """Run deterministic guard skills for SQL actions (must-trigger rules)."""
        if action not in {"execute_duckdb_sql", "execute_context_sql"}:
            return {}
        sql = str(action_input.get("sql", ""))
        if not sql or not re.search(r"\bjoin\b", sql, flags=re.IGNORECASE):
            return {}
        if "skill_join_key_validator" not in self.handlers:
            return {}

        validator_input: dict[str, Any] = {
            "sql": sql,
            "engine": "sqlite" if action == "execute_context_sql" else "duckdb",
        }
        if action == "execute_context_sql" and "path" in action_input:
            validator_input["db_path"] = str(action_input.get("path", ""))

        try:
            guard_result = self.handlers["skill_join_key_validator"](task, validator_input)
            return {
                "join_key_validator": {
                    "ok": guard_result.ok,
                    "content": guard_result.content,
                }
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "join_key_validator": {
                    "ok": False,
                    "content": {
                        "error": f"join validator execution failed: {exc}",
                    },
                }
            }

    def execute(self, task: PublicTask, action: str, action_input: dict[str, Any]) -> ToolExecutionResult:
        """执行指定工具"""
        # 验证工具是否存在
        if action not in self.handlers:
            raise KeyError(f"Unknown tool: {action}")

        guard_info = self._run_sql_guard_skills(task, action, action_input)
        tool_result = self.handlers[action](task, action_input)
        if not guard_info:
            return tool_result

        if isinstance(tool_result.content, dict):
            merged_content = dict(tool_result.content)
            merged_content["_skill_checks"] = guard_info
            return ToolExecutionResult(
                ok=tool_result.ok,
                content=merged_content,
                is_terminal=tool_result.is_terminal,
                answer=tool_result.answer,
            )
        return tool_result


def _search_knowledge_doc(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    query = str(action_input["query"])
    res = search_knowledge_doc(task, query)
    if not isinstance(res, dict):
        res = {"result": res}
    return ToolExecutionResult(ok=True, content=res)


def _list_knowledge_headings(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    query = str(action_input.get("query", ""))
    res = list_knowledge_headings(task, query)
    if not isinstance(res, dict):
        res = {"result": res}
    return ToolExecutionResult(ok=True, content=res)


def _read_knowledge_section(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    heading = str(action_input["heading"])
    res = read_knowledge_section(task, heading)
    if not isinstance(res, dict):
        res = {"result": res}
    return ToolExecutionResult(ok=True, content=res)

def _retrieve_schema_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    query = str(action_input["query"])
    top_k = int(action_input.get("top_k", 10))
    # We expect schema_catalog.json to be in the task output directory, but since we are executing tools, 
    # the SSOT catalog is generated during runner initialization and placed there.
    # Alternatively, the Semantic Validator subagent can generate it in context_dir.
    # We will assume it is available in context_dir or we dynamically generate it if missing.
    from data_agent_baseline.tools.schema_catalog import build_schema_catalog
    
    catalog_path = task.context_dir / "schema_catalog.json"
    if not catalog_path.exists():
        # Generate on the fly if it wasn't saved yet
        catalog = build_schema_catalog(task)
        with open(catalog_path, 'w', encoding='utf-8') as f:
            json.dump(catalog, f, ensure_ascii=False)
            
    res = retrieve_schema_context(catalog_path, query, top_k)
    return ToolExecutionResult(ok=True, content=res)


def _update_plan(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        ok=True,
        content="Plan updated successfully. It is now pinned to your context."
    )

def _get_cheat_sheet(topic: str) -> str:
    """Reads a cheat sheet from the artifacts directory if it exists."""
    cheat_sheet_path = _get_cheat_sheet_dir() / f"{topic}_hints.md"
    if cheat_sheet_path.exists():
        return cheat_sheet_path.read_text(encoding="utf-8")
    return "No cheat sheet available yet. Use update_cheat_sheet to add one if you find a successful query pattern."

def create_default_tool_registry(
    *, 
    allow_python: bool = True, 
    model: Any | None = None,
    config: Any | None = None,
) -> ToolRegistry:
    """
    Creates a ToolRegistry with the standard suite of tools for the primary agent.
    If `model` and `config` are provided, it will also register the subagent tool.
    """
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
        "get_data_schema": ToolSpec(
            name="get_data_schema",
            description="Get the column names and data types of a CSV, JSON, Parquet, or Excel file inside context. ALWAYS use this before querying a file for the first time.",
            input_schema={"path": "relative/path/to/file.csv"},
        ),
        "extract_dataframe_stats": ToolSpec(
            name="extract_dataframe_stats",
            description="Extract statistical information (EDA) from a data file (CSV, Parquet, JSON, Excel) including row/column count, describe(), missing values, and a data sample (head). Use this to get a deeper understanding of the data distribution.",
            input_schema={"path": "relative/path/to/file.csv"},
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
        "list_knowledge_headings": ToolSpec(
            name="list_knowledge_headings",
            description="List available headings from the knowledge documents.",
            input_schema={"query": "Optional query string to filter headings"},
        ),
        "read_knowledge_section": ToolSpec(
            name="read_knowledge_section",
            description="Read a specific section from the knowledge documents by its heading.",
            input_schema={"heading": "The exact heading string"},
        ),
        "search_knowledge_doc": ToolSpec(
            name="search_knowledge_doc",
            description="Search knowledge documents using Hybrid RAG to find relevant information.",
            input_schema={"query": "The search query string"},
        ),
        "retrieve_schema_context": ToolSpec(
            name="retrieve_schema_context",
            description="Use Hybrid RAG (BM25 + Qwen2 Embedding) to search the database Schema Catalog. This retrieves the Top-K most relevant tables, columns, data types, and sampled values for your natural language query. ALWAYS use this to map business terms to actual database fields.",
            input_schema={
                "query": "The natural language query describing what fields or entities you are looking for",
                "top_k": 10
            }
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
        "update_plan": ToolSpec(
            name="update_plan",
            description="Update your persistent task plan/TODO list. Use this to track progress, mark tasks as DONE, and add new sub-tasks. The plan is always visible to you.",
            input_schema={
                "new_plan": "The complete, updated markdown text of your plan."
            }
        ),
    }

    handlers = {
        "answer": _answer,
        "update_cheat_sheet": _update_cheat_sheet,
        "execute_duckdb_sql": _execute_duckdb_sql,
        "execute_context_sql": _execute_context_sql,
        "execute_python": _execute_python,
        "inspect_sqlite_schema": _inspect_sqlite_schema,
        "get_data_schema": _get_data_schema,
        "extract_dataframe_stats": _extract_dataframe_stats,
        "list_context": _list_context,
        "read_csv": _read_csv,
        "read_doc": _read_doc,
        "read_json": _read_json,
        "list_knowledge_headings": _list_knowledge_headings,
        "read_knowledge_section": _read_knowledge_section,
        "search_knowledge_doc": _search_knowledge_doc,
        "retrieve_schema_context": _retrieve_schema_context,
        "update_plan": _update_plan,
    }

    if model and config:
        from data_agent_baseline.orchestrators.langgraph.react_graph import LangGraphReActAgent, LangGraphReActAgentConfig
        from data_agent_baseline.agents.prompt import SUBAGENT_SYSTEM_PROMPT

        def _create_subagent():
            sub_tools = create_default_tool_registry(allow_python=allow_python)
            
            # The subagent needs the `subagent_finish` tool to report back
            sub_tools.specs["subagent_finish"] = ToolSpec(
                name="subagent_finish",
                description="Use this tool when you have completed your data exploration or debugging. Summarize your findings to return them to the Primary Agent.",
                input_schema={
                    "summary": "Detailed summary of your findings or results.",
                    "data_references": ["List of any newly generated files or views.", "Can be empty."],
                    "raw_findings": {"type": "object", "description": "Any structured JSON data to return."}
                }
            )
            sub_tools.handlers["subagent_finish"] = subagent_finish

            return LangGraphReActAgent(
                model=model,
                tools=sub_tools,
                config=LangGraphReActAgentConfig(
                    max_steps=getattr(config.agent, "max_steps", 64) // 2, # Give subagent less steps
                    history_max_steps=getattr(config.agent, "history_max_steps", 16),
                    max_prompt_chars=getattr(config.agent, "max_prompt_chars", 24000),
                    compress_trigger_steps=getattr(config.agent, "compress_trigger_steps", 12),
                ),
                system_prompt=SUBAGENT_SYSTEM_PROMPT,
            )

        specs["delegate_to_data_analyst"] = ToolSpec(
            name="delegate_to_data_analyst",
            description="Delegate complex data exploration, SQL debugging, or deep analysis to a specialized Subagent. Use this when you are stuck or need to perform a multi-step data task.",
            input_schema={
                "sub_task_description": "Detailed instructions of what the subagent needs to find or compute.",
                "focus_files": ["file1.csv", "file2.json"]
            },
        )
        handlers["delegate_to_data_analyst"] = build_subagent_tool(_create_subagent)

    registry = ToolRegistry(specs=specs, handlers=handlers)
    
    # Load dynamic skills
    from data_agent_baseline.tools.skills import load_skills
    load_skills(registry)
    
    return registry
