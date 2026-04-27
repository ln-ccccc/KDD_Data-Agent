from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.filesystem import resolve_context_path, get_data_schema
from data_agent_baseline.tools.sqlite import inspect_sqlite_schema, execute_read_only_sql


def build_schema_catalog(task: PublicTask) -> dict[str, Any]:
    """
    遍历 context 目录，自动提取所有支持的数据文件（SQLite, CSV, Parquet, Excel, JSON）的 Schema，
    并采样 DISTINCT 值，最终生成全局唯一的 SchemaCatalog (SSOT)。
    """
    catalog: dict[str, Any] = {
        "task_id": task.id,
        "context_dir": str(task.context_dir),
        "tables": []
    }

    if not task.context_dir.exists() or not task.context_dir.is_dir():
        return catalog

    # 遍历 context_dir 下的所有文件
    for filepath in task.context_dir.rglob("*"):
        if not filepath.is_file():
            continue

        rel_path = filepath.relative_to(task.context_dir).as_posix()
        ext = filepath.suffix.lower()

        # 处理 SQLite 数据库
        if ext in [".sqlite", ".db", ".sqlite3"]:
            try:
                sqlite_schema = inspect_sqlite_schema(filepath)
                # sqlite_schema 返回格式通常包含表名、建表语句、列信息等
                # 我们进一步探测 SQLite 表的枚举值
                tables_info = sqlite_schema.get("tables", [])
                if isinstance(tables_info, list):
                    for table in tables_info:
                        table_name = table.get("name")
                        columns = table.get("columns", [])
                        
                        enhanced_columns = []
                        for col in columns:
                            col_name = col.get("name")
                            col_type = col.get("type", "").upper()
                            
                            sample_values = []
                            # 对可能包含枚举值的列进行采样
                            if "TEXT" in col_type or "VARCHAR" in col_type or "INT" in col_type:
                                sql = f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" IS NOT NULL LIMIT 10'
                                try:
                                    res = execute_read_only_sql(filepath, sql)
                                    if res.get("success"):
                                        sample_values = [row[0] for row in res.get("rows", []) if row[0] is not None]
                                except Exception:
                                    pass
                            
                            col_info = dict(col)
                            col_info["sample_values"] = sample_values
                            enhanced_columns.append(col_info)
                        
                        catalog["tables"].append({
                            "source_file": rel_path,
                            "source_type": "sqlite",
                            "table_name": table_name,
                            "sql": table.get("sql", ""),
                            "columns": enhanced_columns
                        })
            except Exception as e:
                print(f"Error inspecting SQLite {rel_path}: {e}")

        # 处理结构化文件 (CSV, Parquet, JSON, Excel)
        elif ext in [".csv", ".parquet", ".json", ".xlsx", ".xls"]:
            try:
                # 复用已经增强过的 get_data_schema (包含了 DuckDB DISTINCT 采样)
                file_schema = get_data_schema(task, rel_path)
                if file_schema.get("schema") and file_schema.get("columns"):
                    schema_rows = file_schema["schema"]
                    cols_header = file_schema["columns"]
                    
                    columns_info = []
                    for row in schema_rows:
                        # row 格式: [column_name, column_type, null, key, default, extra, distinct_samples]
                        # 依具体 DuckDB DESCRIBE 格式而定，至少前两个是 name 和 type
                        col_name = row[0] if len(row) > 0 else "unknown"
                        col_type = row[1] if len(row) > 1 else "unknown"
                        
                        # 在 get_data_schema 中，我们把 samples 放在了最后一行
                        samples_str = row[-1] if len(row) > 2 and isinstance(row[-1], str) and row[-1].startswith("Sample values:") else ""
                        
                        columns_info.append({
                            "name": col_name,
                            "type": col_type,
                            "sample_values_str": samples_str
                        })
                    
                    catalog["tables"].append({
                        "source_file": rel_path,
                        "source_type": ext.strip("."),
                        "table_name": Path(rel_path).stem, # 默认用文件名作为表名
                        "columns": columns_info
                    })
            except Exception as e:
                print(f"Error inspecting file {rel_path}: {e}")

    return catalog


def save_schema_catalog(task: PublicTask, output_dir: Path) -> Path:
    """
    生成 SchemaCatalog 并保存到指定目录（通常是 task_output_dir），
    返回生成的 JSON 文件路径。
    """
    catalog = build_schema_catalog(task)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / "schema_catalog.json"
    
    with catalog_path.open("w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
        
    return catalog_path
