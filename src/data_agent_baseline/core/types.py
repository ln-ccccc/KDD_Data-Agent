# types for data agent baseline
"""
定义数据代理基准线的类型，包括任务记录、任务资产、公共任务、工具观察元数据、重试策略等。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict


JsonValue = Any


TraceLevel = Literal["minimal", "standard", "verbose"]


class ToolObservationMeta(TypedDict, total=False):
    tool_elapsed_seconds: float
    llm_elapsed_seconds: float
    truncated: bool
    notes: str


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry policy for transient failures (LLM calls, etc.)."""

    max_retries: int = 2  # total attempts = 1 + max_retries
    backoff_seconds: float = 0.8

    def normalized(self) -> "RetryPolicy":
        max_retries = max(int(self.max_retries), 0)
        backoff_seconds = max(float(self.backoff_seconds), 0.0)
        return RetryPolicy(max_retries=max_retries, backoff_seconds=backoff_seconds)

