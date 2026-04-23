from __future__ import annotations

import csv
import json
from pathlib import Path

from data_agent_baseline.benchmark.schema import PublicTask

# convert relative path to absolute path in context dir 【LLM对相对路径没有稳定语义锚点，容易漂移】
# 【https://www.anthropic.com/engineering/building-effective-agents?utm_source=chatgpt.com】
def resolve_context_path(task: PublicTask, relative_path: str) -> Path:
    candidate = (task.context_dir / relative_path).resolve()
    context_root = task.context_dir.resolve()
    if context_root not in candidate.parents and candidate != context_root:
        raise ValueError(f"Path escapes context dir: {relative_path}")
    if not candidate.exists():
        raise FileNotFoundError(f"Missing context asset: {relative_path}")
    return candidate

# list context tree in relative path
def list_context_tree(task: PublicTask, *, max_depth: int = 4) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    # walk the context tree
    def walk(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        # sort by directory first, then by name
        for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name)):
            rel_path = child.relative_to(task.context_dir).as_posix()
            entries.append(
                {
                    "path": rel_path,
                    "kind": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
            if child.is_dir():
                walk(child, depth + 1)

    walk(task.context_dir, 1)
    # relative path to context dir
    return {
        "root": str(task.context_dir),
        "entries": entries,
    }

# 读取CSV文件预览，用于快速查看CSV文件的内容
def read_csv_preview(task: PublicTask, relative_path: str, *, max_rows: int = 20) -> dict[str, object]:
    path = resolve_context_path(task, relative_path)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        rows = list(reader)

    if not rows:
        return {
            "path": relative_path,
            "columns": [],
            "rows": [],
            "row_count": 0,
        }

    header = rows[0]
    data_rows = rows[1:]
    return {
        "path": relative_path,
        "columns": header,
        "rows": data_rows[:max_rows],
        "row_count": len(data_rows),
    }

# 读取JSON文件预览，用于快速查看JSON文件的内容
def read_json_preview(task: PublicTask, relative_path: str, *, max_chars: int = 4000) -> dict[str, object]:
    path = resolve_context_path(task, relative_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), strict=False)
    except json.JSONDecodeError:
        # If it's not valid JSON, just read as text
        return read_doc_preview(task, relative_path, max_chars=max_chars)
    preview = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "path": relative_path,
        "preview": preview[:max_chars],
        "truncated": len(preview) > max_chars,
    }

# 读取DOCX文件预览，用于快速查看DOCX文件的内容
def read_doc_preview(task: PublicTask, relative_path: str, *, max_chars: int = 4000) -> dict[str, object]:
    path = resolve_context_path(task, relative_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": relative_path,
        "preview": text[:max_chars],
        "truncated": len(text) > max_chars,
    }


def get_data_schema(task: PublicTask, relative_path: str) -> dict[str, object]:
    """
    Get the schema (columns and types) of a data file using DuckDB.
    Supports CSV, JSON, Parquet, and Excel.
    """
    from data_agent_baseline.tools.duckdb_exec import execute_duckdb_sql
    
    path = resolve_context_path(task, relative_path)
    ext = path.suffix.lower()
    
    if ext in [".csv", ".parquet", ".json", ".xlsx", ".xls"]:
        # Use DuckDB DESCRIBE to get a clean schema
        sql = f"DESCRIBE SELECT * FROM '{relative_path}'"
        res = execute_duckdb_sql(task.context_dir, sql)
        if res.get("success"):
            return {
                "path": relative_path,
                "schema": res.get("rows"),
                "columns": res.get("columns"),
                "note": "Schema obtained via DuckDB DESCRIBE."
            }
        else:
            return {
                "path": relative_path,
                "error": res.get("error"),
                "note": "Failed to get schema via DuckDB."
            }
    
    return {
        "path": relative_path,
        "error": f"Unsupported file extension for schema extraction: {ext}",
    }
