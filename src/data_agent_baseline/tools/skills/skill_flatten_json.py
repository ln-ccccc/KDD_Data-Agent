import json
from typing import Any
from pathlib import Path

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.types import ToolExecutionResult, ToolSpec
from data_agent_baseline.tools.filesystem import resolve_context_path

TOOL_SPEC = ToolSpec(
    name="skill_flatten_json_schema",
    description="Skill: Quickly parses a complex nested JSON file and returns a flattened representation of its schema. Useful for understanding deeply nested JSON arrays/objects without writing custom Python code.",
    input_schema={
        "file_path": "Relative path to the JSON file."
    }
)

def TOOL_HANDLER(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """
    Skill handler to flatten a JSON schema.
    """
    try:
        path_str = action_input.get("file_path")
        if not path_str:
            return ToolExecutionResult(ok=False, content={"error": "file_path is required."})

        full_path = resolve_context_path(task, path_str)
        if not full_path.exists():
            return ToolExecutionResult(ok=False, content={"error": f"File not found: {path_str}"})

        with open(full_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Basic flattener logic
        def flatten_schema(obj, prefix="", max_depth=5, current_depth=0):
            if current_depth > max_depth:
                return {prefix: "MAX_DEPTH_REACHED"}
            
            schema = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    new_key = f"{prefix}.{k}" if prefix else k
                    schema.update(flatten_schema(v, new_key, max_depth, current_depth + 1))
            elif isinstance(obj, list):
                if len(obj) > 0:
                    schema.update(flatten_schema(obj[0], f"{prefix}[0]", max_depth, current_depth + 1))
                else:
                    schema[f"{prefix}[]"] = "Empty List"
            else:
                schema[prefix] = type(obj).__name__
            return schema

        # Handle list of objects at root
        if isinstance(data, list) and len(data) > 0:
            schema_out = flatten_schema(data[0], prefix="root[0]")
        else:
            schema_out = flatten_schema(data, prefix="root")

        return ToolExecutionResult(
            ok=True,
            content={
                "message": "Successfully extracted flattened JSON schema.",
                "flattened_schema": schema_out
            }
        )

    except Exception as e:
        import traceback
        return ToolExecutionResult(ok=False, content={"error": str(e), "traceback": traceback.format_exc()})
