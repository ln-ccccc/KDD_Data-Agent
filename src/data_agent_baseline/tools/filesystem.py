from __future__ import annotations

import csv
import json
from pathlib import Path

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.runtime.cache import SCHEMA_CACHE, context_namespace, file_fingerprint

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
    Also provides a sample of distinct values for categorical/string columns to prevent hallucination.
    """
    from data_agent_baseline.tools.duckdb_exec import execute_duckdb_sql

    path = resolve_context_path(task, relative_path)
    ext = path.suffix.lower()
    namespace = context_namespace(task.context_dir)
    fp = file_fingerprint(path)
    schema_cache_key = f"schema:{fp}"
    cached = SCHEMA_CACHE.get(schema_cache_key)
    if isinstance(cached, dict):
        return dict(cached)

    if ext in [".csv", ".parquet", ".json", ".xlsx", ".xls"]:
        # Use DuckDB DESCRIBE to get a clean schema
        # For JSON, we might need read_json_auto if it's an array of records
        
        # Determine base query based on file type to handle nested JSON like {"records": [...]}
        if ext == ".json":
            base_query = f"SELECT * FROM (SELECT unnest(records) as r FROM read_json_auto('{relative_path}'))"
            # Try to see if it's nested
            test_res = execute_duckdb_sql(task.context_dir, f"SELECT records FROM read_json_auto('{relative_path}') LIMIT 1")
            if not test_res.get("success"):
                base_query = f"SELECT * FROM '{relative_path}'"
        else:
            base_query = f"SELECT * FROM '{relative_path}'"
            
        sql = f"DESCRIBE {base_query}"
        res = execute_duckdb_sql(task.context_dir, sql)
        
        if res.get("success"):
            schema_rows = res.get("rows", [])
            schema_cols = res.get("columns", [])
            
            # Enhance schema with distinct values for VARCHAR/String columns
            enhanced_schema = []
            for row in schema_rows:
                col_name = row[0]
                col_type = row[1]
                
                # If it's a string/varchar or integer type, fetch some distinct values
                distinct_values = []
                if "VARCHAR" in str(col_type).upper() or "INTEGER" in str(col_type).upper() or "BIGINT" in str(col_type).upper():
                    # Handle column names with spaces or special characters by quoting them
                    safe_col_name = f'"{col_name}"' if not col_name.startswith('r.') else col_name
                    if base_query.startswith("SELECT * FROM (SELECT unnest(records)"):
                        # Special handling for nested JSON columns
                        val_sql = f"SELECT DISTINCT r.{safe_col_name} FROM (SELECT unnest(records) as r FROM read_json_auto('{relative_path}')) WHERE r.{safe_col_name} IS NOT NULL LIMIT 10"
                    else:
                        val_sql = f"SELECT DISTINCT {safe_col_name} FROM '{relative_path}' WHERE {safe_col_name} IS NOT NULL LIMIT 10"
                        
                    val_res = execute_duckdb_sql(task.context_dir, val_sql)
                    if val_res.get("success"):
                        distinct_values = [v[0] for v in val_res.get("rows", [])]
                    else:
                        # Fallback if DISTINCT fails (e.g. nested lists)
                        pass
                
                # Create a new row with distinct values appended
                enhanced_row = list(row)
                if distinct_values:
                    # Truncate long strings for the preview
                    formatted_vals = [str(v)[:50] + "..." if len(str(v)) > 50 else str(v) for v in distinct_values]
                    enhanced_row.append(f"Sample values: {formatted_vals}")
                else:
                    enhanced_row.append("")
                    
                enhanced_schema.append(enhanced_row)
            
            # Add the new column to headers
            schema_cols.append("Distinct_Samples")
            
            result = {
                "path": relative_path,
                "schema": enhanced_schema,
                "columns": schema_cols,
                "note": "Schema obtained via DuckDB DESCRIBE. Categorical columns include distinct value samples to help you formulate exact WHERE clauses without guessing."
            }
            SCHEMA_CACHE.set(schema_cache_key, result, namespace=namespace)
            return result
        else:
            result = {
                "path": relative_path,
                "error": res.get("error"),
                "note": "Failed to get schema via DuckDB."
            }
            SCHEMA_CACHE.set(schema_cache_key, result, namespace=namespace)
            return result

    result = {
        "path": relative_path,
        "error": f"Unsupported file extension for schema extraction: {ext}",
    }
    SCHEMA_CACHE.set(schema_cache_key, result, namespace=namespace)
    return result
