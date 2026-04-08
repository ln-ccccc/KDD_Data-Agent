from __future__ import annotations

import duckdb
from pathlib import Path
import os

# Define a project-level extensions directory
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DUCKDB_EXT_DIR = PROJECT_ROOT / ".duckdb_extensions"
DUCKDB_EXT_DIR.mkdir(exist_ok=True)

# Pre-install extensions once at module load to avoid repeated downloads and permissions issues per task
_init_conn = duckdb.connect(':memory:', config={
    'extension_directory': DUCKDB_EXT_DIR.as_posix()
})
try:
    _init_conn.execute("INSTALL json;")
    _init_conn.execute("INSTALL sqlite;")
    _init_conn.execute("INSTALL excel;")
except Exception as e:
    # It might fail if no internet, but we try our best
    pass
finally:
    _init_conn.close()


def execute_duckdb_sql(context_root: Path, sql: str, *, limit: int = 200) -> dict[str, object]:
    """
    Execute a DuckDB SQL query in the context directory.
    This allows querying files (CSV, Parquet, JSON, etc.) natively via DuckDB.
    """
    # Use an in-memory database
    # Point extension_directory to the shared project-level directory
    # Set home_directory to avoid permission issues
    conn = duckdb.connect(':memory:', config={
        'extension_directory': DUCKDB_EXT_DIR.as_posix(),
        'home_directory': context_root.resolve().as_posix()
    })
    try:
        # Load extensions by default
        conn.execute("LOAD json;")
        
        try:
            conn.execute("LOAD sqlite;")
            conn.execute("LOAD excel;")
        except Exception:
            pass

        # Set file_search_path to context_root so relative file paths in queries resolve correctly
        resolved_context = context_root.resolve().as_posix()
        conn.execute(f"SET file_search_path = '{resolved_context}'")
        
        # Enforce read-only for file operations (optional but good practice for queries)
        # Note: If we need to write out files, we shouldn't enable this.
        # But for data agent analysis, we often just read and return rows.
        # We can leave it open for now based on the "competition score focus" memory,
        # but read-only is safer if we just want `SELECT`.
        # For now, let's just let it execute.
        
        cursor = conn.execute(sql)
        
        if cursor.description is None:
            # e.g., for PRAGMA or statements without a result set
            return {
                "success": True,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
            }

        column_names = [item[0] for item in cursor.description]
        rows = cursor.fetchmany(limit + 1)
        
        truncated = len(rows) > limit
        limited_rows = rows[:limit]
        
        # Convert values to strings to ensure JSON serialization compatibility
        # Models only need text representation, and date/datetime objects will break JSON serialization
        stringified_rows = []
        for row in limited_rows:
            stringified_rows.append([str(val) if val is not None else None for val in row])
        
        return {
            "success": True,
            "columns": column_names,
            "rows": stringified_rows,
            "row_count": len(limited_rows),
            "truncated": truncated,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }
    finally:
        conn.close()
