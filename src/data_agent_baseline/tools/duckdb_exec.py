from __future__ import annotations

import duckdb
from pathlib import Path
import os
import json

# Define a project-level extensions directory
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DUCKDB_EXT_DIR = PROJECT_ROOT / ".duckdb_extensions"
DUCKDB_EXT_DIR.mkdir(exist_ok=True)

# Extension capability mapping (Static for module, updated per connection)
CAPABILITIES = {
    "json": False,
    "sqlite": False,
    "excel": False
}

# Pre-install extensions once at module load
_init_conn = duckdb.connect(':memory:', config={
    'extension_directory': DUCKDB_EXT_DIR.as_posix()
})
try:
    _init_conn.execute("SET autoinstall_known_extensions=false;")
    _init_conn.execute("INSTALL json;")
    _init_conn.execute("INSTALL sqlite;")
    _init_conn.execute("INSTALL excel;")
except Exception:
    pass
finally:
    _init_conn.close()


def _get_conn(context_root: Path):
    """Create a DuckDB connection with consistent configuration."""
    conn = duckdb.connect(':memory:', config={
        'extension_directory': DUCKDB_EXT_DIR.as_posix(),
        'home_directory': context_root.resolve().as_posix()
    })
    conn.execute("SET autoinstall_known_extensions=false;")
    
    # Try to load and update capabilities
    for ext in CAPABILITIES:
        try:
            conn.execute(f"LOAD {ext};")
            CAPABILITIES[ext] = True
        except Exception:
            CAPABILITIES[ext] = False
            
    resolved_context = context_root.resolve().as_posix()
    conn.execute(f"SET file_search_path = '{resolved_context}'")
    return conn


def execute_duckdb_sql(context_root: Path, sql: str, *, limit: int = 200) -> dict[str, object]:
    """
    Execute a DuckDB SQL query with structured status and action constraints.
    Moves from 'prompting' to 'constraint-based' design.
    """
    conn = _get_conn(context_root)
    try:
        cursor = conn.execute(sql)
        
        # Result set check
        if cursor.description is None:
            return {
                "success": True,
                "status": "SUCCESS",
                "row_count": 0,
                "columns": [],
                "rows": [],
                "capabilities": CAPABILITIES
            }

        column_names = [item[0] for item in cursor.description]
        rows = cursor.fetchmany(limit + 1)
        
        truncated = len(rows) > limit
        limited_rows = rows[:limit]
        
        # Serialization safety
        stringified_rows = []
        for row in limited_rows:
            stringified_rows.append([str(val) if val is not None else None for val in row])

        result = {
            "success": True,
            "status": "SUCCESS",
            "columns": column_names,
            "rows": stringified_rows,
            "row_count": len(limited_rows),
            "truncated": truncated,
            "capabilities": CAPABILITIES
        }

        # 0-row handling (Zero Result Constraint)
        if len(limited_rows) == 0 and sql.strip().upper().startswith("SELECT"):
            result["status"] = "ZERO_RESULT"
            result["allowed_next_actions"] = [
                "inspect_sqlite_schema",
                "get_data_schema",
                "execute_duckdb_sql (using SELECT DISTINCT to verify values)",
                "read_csv (verify raw data spelling/case)",
                "read_doc (check business rules in knowledge.md)"
            ]
            
        return result

    except Exception as e:
        err_msg = str(e)
        error_type = "EXECUTION_ERROR"
        allowed_actions = ["list_context", "get_data_schema", "inspect_sqlite_schema"]
        blocked_ops = []

        # Error Classification Logic
        if "Parser Error" in err_msg or "syntax error" in err_msg.lower():
            error_type = "SYNTAX_ERROR"
            allowed_actions = ["Check DuckDB SQL syntax rules", "Simplify the query"]
        elif "Catalog Error" in err_msg:
            if "does not exist" in err_msg or "not found" in err_msg:
                error_type = "SCHEMA_ERROR"
                allowed_actions = ["get_data_schema", "inspect_sqlite_schema", "Verify table/column names"]
        elif "IO Error" in err_msg or "No such file or directory" in err_msg:
            error_type = "FILE_NOT_FOUND"
            allowed_actions = ["list_context", "Check relative file path in context/"]
        
        # Extension capability check
        if "sqlite" in err_msg.lower() and not CAPABILITIES["sqlite"]:
            error_type = "UNSUPPORTED_FILE_FORMAT"
            blocked_ops.append("READ_SQLITE")
            allowed_actions = ["Use execute_python with sqlite3 library"]
        elif "excel" in err_msg.lower() and not CAPABILITIES["excel"]:
            error_type = "UNSUPPORTED_FILE_FORMAT"
            blocked_ops.append("READ_EXCEL")
            allowed_actions = ["Use execute_python with pandas read_excel"]

        return {
            "success": False,
            "status": "ERROR",
            "error_type": error_type,
            "error_message": err_msg,
            "blocked_operations": blocked_ops,
            "allowed_next_actions": allowed_actions,
            "capabilities": CAPABILITIES
        }
    finally:
        conn.close()
