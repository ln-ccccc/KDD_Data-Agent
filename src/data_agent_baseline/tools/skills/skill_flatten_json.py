import json
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolExecutionResult, ToolSpec
from data_agent_baseline.tools.filesystem import resolve_context_path

TOOL_SPEC = ToolSpec(
    name="skill_json_structure",
    description=(
        "Skill: Analyze JSON structure only when needed (complex nesting or JSON path failures). "
        "Returns root type, array paths, date-like field samples, and numeric-string field candidates."
    ),
    input_schema={
        "file_path": "Relative path to the JSON file.",
        "max_depth": "Optional max nesting depth to inspect. Default 5.",
    }
)

def TOOL_HANDLER(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """
    Skill handler for lightweight JSON structure analysis.
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

        max_depth = int(action_input.get("max_depth", 5))

        def walk(obj: Any, prefix: str, current_depth: int, out_paths: list[str], out_samples: dict[str, str], out_numeric_strings: list[str]) -> None:
            if current_depth > max_depth:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    new_key = f"{prefix}.{k}" if prefix else k
                    walk(v, new_key, current_depth + 1, out_paths, out_samples, out_numeric_strings)
            elif isinstance(obj, list):
                out_paths.append(prefix or "$")
                if obj:
                    walk(obj[0], f"{prefix}[*]" if prefix else "$[*]", current_depth + 1, out_paths, out_samples, out_numeric_strings)
            elif isinstance(obj, str):
                # Date-like sample
                if ("-" in obj and ":" in obj) or ("T" in obj and "Z" in obj):
                    out_samples.setdefault(prefix, obj)
                # Numeric string trap sample
                compact = obj.replace(",", "").replace(".", "").replace("-", "")
                if compact.isdigit():
                    if prefix not in out_numeric_strings:
                        out_numeric_strings.append(prefix)

        array_paths: list[str] = []
        date_field_samples: dict[str, str] = {}
        numeric_string_fields: list[str] = []
        walk(data, "$", 0, array_paths, date_field_samples, numeric_string_fields)

        root_type = "array" if isinstance(data, list) else "object" if isinstance(data, dict) else "scalar"

        return ToolExecutionResult(
            ok=True,
            content={
                "root_type": root_type,
                "array_paths": sorted(set(array_paths)),
                "date_field_samples": date_field_samples,
                "numeric_string_fields": numeric_string_fields,
            }
        )

    except Exception as e:
        import traceback
        return ToolExecutionResult(ok=False, content={"error": str(e), "traceback": traceback.format_exc()})
