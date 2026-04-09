from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.agents.prompt import (
    REACT_SYSTEM_PROMPT,
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.react import parse_model_step
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class LangGraphReActAgentConfig:
    max_steps: int = 16


class _GraphState(TypedDict, total=False):
    task: PublicTask
    state: AgentRuntimeState
    step_index: int
    system_prompt: str
    stop_reason: str
    last_raw_response: str
    last_llm_elapsed: float


class LangGraphReActAgent:
    """LangGraph-based ReAct agent (JSON action protocol preserved)."""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: LangGraphReActAgentConfig | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or LangGraphReActAgentConfig()
        self.system_prompt = system_prompt or REACT_SYSTEM_PROMPT
        self._app = self._build_graph()

    def _build_messages(
        self, task: PublicTask, runtime_state: AgentRuntimeState, system_prompt: str
    ) -> list[ModelMessage]:
        system_content = build_system_prompt(
            self.tools.describe_for_prompt(),
            system_prompt=system_prompt,
        )
        messages = [ModelMessage(role="system", content=system_content)]
        messages.append(ModelMessage(role="user", content=build_task_prompt(task)))
        for step in runtime_state.steps:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(ModelMessage(role="user", content=build_observation_prompt(step.observation)))
        return messages

    def _llm_step(self, gs: _GraphState) -> _GraphState:
        task = gs["task"]
        runtime_state = gs["state"]
        system_prompt = gs.get("system_prompt") or self.system_prompt

        llm_started_at = perf_counter()
        raw_response = self.model.complete(self._build_messages(task, runtime_state, system_prompt))
        llm_elapsed = round(perf_counter() - llm_started_at, 6)

        gs["last_raw_response"] = raw_response
        gs["last_llm_elapsed"] = llm_elapsed
        return gs

    def _tool_step(self, gs: _GraphState) -> _GraphState:
        task = gs["task"]
        runtime_state = gs["state"]
        step_index = int(gs.get("step_index", 0)) + 1
        gs["step_index"] = step_index

        raw_response = str(gs.get("last_raw_response", ""))
        llm_elapsed = float(gs.get("last_llm_elapsed", 0.0))

        try:
            model_step = parse_model_step(raw_response)
            tool_started_at = perf_counter()
            tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
            tool_elapsed = round(perf_counter() - tool_started_at, 6)

            truncated = False
            if isinstance(tool_result.content, dict):
                truncated = bool(tool_result.content.get("truncated", False))

            observation: dict[str, Any] = {
                "ok": tool_result.ok,
                "tool": model_step.action,
                "content": tool_result.content,
                "meta": {
                    "llm_elapsed_seconds": llm_elapsed,
                    "tool_elapsed_seconds": tool_elapsed,
                    "truncated": truncated,
                },
            }

            runtime_state.steps.append(
                StepRecord(
                    step_index=step_index,
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=model_step.action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                    llm_elapsed_seconds=llm_elapsed,
                    tool_elapsed_seconds=tool_elapsed,
                )
            )

            if tool_result.is_terminal:
                runtime_state.answer = tool_result.answer
                gs["stop_reason"] = "submitted_answer"
                return gs

        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            if "Invalid control character" in error_msg:
                error_msg += " - DO NOT use literal newlines inside JSON strings. Use \\n instead."

            observation = {
                "ok": False,
                "error": error_msg,
                "meta": {"llm_elapsed_seconds": llm_elapsed},
            }
            runtime_state.steps.append(
                StepRecord(
                    step_index=step_index,
                    thought="",
                    action="__error__",
                    action_input={},
                    raw_response=raw_response,
                    observation=observation,
                    ok=False,
                    llm_elapsed_seconds=llm_elapsed,
                    tool_elapsed_seconds=None,
                )
            )

        if step_index >= self.config.max_steps:
            runtime_state.failure_reason = "Agent did not submit an answer within max_steps."
            gs["stop_reason"] = "max_steps"
        return gs

    def _should_continue(self, gs: _GraphState) -> str:
        runtime_state = gs["state"]
        if runtime_state.answer is not None:
            return "end"
        if runtime_state.failure_reason is not None:
            return "end"
        if int(gs.get("step_index", 0)) >= self.config.max_steps:
            return "end"
        return "continue"

    def _build_graph(self):
        g = StateGraph(_GraphState)
        g.add_node("llm", self._llm_step)
        g.add_node("tool", self._tool_step)
        g.set_entry_point("llm")
        g.add_edge("llm", "tool")
        g.add_conditional_edges("tool", self._should_continue, {"continue": "llm", "end": END})
        return g.compile()

    def run(self, task: PublicTask) -> AgentRunResult:
        runtime_state = AgentRuntimeState()
        _ = self._app.invoke(
            {
                "task": task,
                "state": runtime_state,
                "step_index": 0,
                "system_prompt": self.system_prompt,
            }
        )

        if runtime_state.answer is None and runtime_state.failure_reason is None:
            runtime_state.failure_reason = "Agent stopped without an answer."

        return AgentRunResult(
            task_id=task.task_id,
            answer=runtime_state.answer,
            steps=list(runtime_state.steps),
            failure_reason=runtime_state.failure_reason,
        )

