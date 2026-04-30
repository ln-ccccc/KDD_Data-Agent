__all__ = [
    "ToolExecutionResult",
    "ToolRegistry",
    "ToolSpec",
    "create_default_tool_registry",
]


def __getattr__(name: str):
    if name in {"ToolRegistry", "create_default_tool_registry"}:
        from data_agent_baseline.tools.registry import ToolRegistry, create_default_tool_registry

        return {"ToolRegistry": ToolRegistry, "create_default_tool_registry": create_default_tool_registry}[name]
    if name in {"ToolExecutionResult", "ToolSpec"}:
        from data_agent_baseline.tools.types import ToolExecutionResult, ToolSpec

        return {"ToolExecutionResult": ToolExecutionResult, "ToolSpec": ToolSpec}[name]
    raise AttributeError(name)
