from __future__ import annotations


class DABenchError(Exception):
    """Base class for all baseline errors."""


class ConfigError(DABenchError):
    """Raised when config is invalid or missing required values."""


class ModelError(DABenchError):
    """Raised when the model provider fails or returns invalid responses."""


class ToolError(DABenchError):
    """Raised when a tool invocation fails unexpectedly."""


class PlanningError(DABenchError):
    """Raised when planner output is invalid."""

