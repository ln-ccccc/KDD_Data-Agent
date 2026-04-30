import json
import sqlite3
from typing import Any
from pathlib import Path

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.types import ToolExecutionResult, ToolSpec
from data_agent_baseline.tools.filesystem import resolve_context_path

TOOL_SPEC = ToolSpec(
    name="skill_fuzzy_find_column",
    description="Skill: Perform a fuzzy search across all columns in a SQLite database to find columns that might match a specific keyword. Use this when you get 'no such column' errors and aren't sure of the exact column name or which table it's in.",
    input_schema={
        "db_path": "Relative path to the .sqlite or .db file.",
        "keyword": "The keyword to search for in column names (case-insensitive)."
    }
)

def TOOL_HANDLER(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """
    Skill handler to fuzzy search for columns in an SQLite database.
    """
    try:
        db_path = action_input.get("db_path")
        keyword = action_input.get("keyword", "").lower()
        
        if not db_path or not keyword:
            return ToolExecutionResult(ok=False, content={"error": "db_path and keyword are required."})

        full_path = resolve_context_path(task, db_path)
        if not full_path.exists():
            return ToolExecutionResult(ok=False, content={"error": f"Database file not found: {db_path}"})

        conn = sqlite3.connect(str(full_path))
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]

        matches = []
        for table in tables:
            cursor.execute(f"PRAGMA table_info(`{table}`);")
            columns = [row[1] for row in cursor.fetchall()]
            for col in columns:
                if keyword in col.lower():
                    matches.append({"table": table, "column": col})

        conn.close()

        if not matches:
            return ToolExecutionResult(
                ok=True,
                content={"message": f"No columns found containing the keyword '{keyword}' in any table."}
            )

        return ToolExecutionResult(
            ok=True,
            content={
                "message": f"Found {len(matches)} matching column(s).",
                "matches": matches
            }
        )

    except Exception as e:
        import traceback
        return ToolExecutionResult(ok=False, content={"error": str(e), "traceback": traceback.format_exc()})
