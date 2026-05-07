from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuntimeStep(BaseModel):
    model_config = ConfigDict(extra="allow")

    step_index: int
    thought: str = ""
    action: str
    action_input: dict[str, Any] = Field(default_factory=dict)
    raw_response: str = ""
    observation: dict[str, Any] = Field(default_factory=dict)
    ok: bool = False
    llm_elapsed_seconds: float | None = None
    tool_elapsed_seconds: float | None = None


class AbstractMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assets: dict[str, list[str]] = Field(default_factory=lambda: {"files": [], "tables": []})
    facts: list[dict[str, Any]] = Field(default_factory=list)  # {k,v,source}
    open_questions: list[str] = Field(default_factory=list)


class RuntimeState(BaseModel):
    """LangGraph SSOT state schema (versioned)."""

    model_config = ConfigDict(extra="allow")

    state_version: int = 1

    # Execution
    steps: list[RuntimeStep] = Field(default_factory=list)
    pending_answer: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    failure_reason: str | None = None

    # LLM / prompt control
    current_plan: str = ""
    abstract: AbstractMemory = Field(default_factory=AbstractMemory)
    prompt_window_steps: int = 8
    steps_since_compress: int = 0

    # Last LLM output
    last_raw_response: str = ""
    last_llm_elapsed: float = 0.0

    # Control / feedback signals
    last_error_key: str | None = None
    error_streak: int = 0
    error_history: list[str] = Field(default_factory=list)
    strategy_switch_count: int = 0
    force_finalize: bool = False
    force_finalize_reason: str | None = None
    recall_cooldown_steps: int = 0
    pending_auto_recall: dict[str, Any] | None = None
    recall_snippets: list[dict[str, Any]] = Field(default_factory=list)

    def to_langgraph_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")


def coerce_state(state: dict[str, Any]) -> RuntimeState:
    """Validate and coerce a raw LangGraph dict into RuntimeState."""
    return RuntimeState.model_validate(state)
