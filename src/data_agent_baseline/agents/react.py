from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage, ModelStep
from data_agent_baseline.agents.prompt import (
    REACT_SYSTEM_PROMPT,
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolRegistry

@dataclass(frozen=True, slots=True)
class ReActAgentConfig:
    max_steps: int = 64
    max_prompt_chars: int = 24_000
    compress_trigger_steps: int = 12

def _strip_json_fence(raw_response: str) -> str:
    text = raw_response.strip()
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence_match is not None:
        return fence_match.group(1).strip()
    generic_fence_match = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic_fence_match is not None:
        return generic_fence_match.group(1).strip()
    return text


def _load_single_json_object(text: str) -> dict[str, object]:
    try:
        # Strict loading first
        payload, end = json.JSONDecoder(strict=False).raw_decode(text)
    except json.JSONDecodeError:
        # If it fails, try to aggressively clean up control characters
        # Common issue: models putting literal newlines inside JSON string values instead of \n
        cleaned_text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
        payload, end = json.JSONDecoder(strict=False).raw_decode(cleaned_text)
        
    remainder = text[end:].strip()
    if remainder:
        cleaned_remainder = re.sub(r"(?:\\[nrt])+", "", remainder).strip()
        if cleaned_remainder:
            raise ValueError("Model response must contain only one JSON object.")
    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")
    return payload


def parse_model_step(raw_response: str) -> ModelStep:
    normalized = _strip_json_fence(raw_response)
    
    # Sometimes model might generate invalid JSON, wrap in try/except
    try:
        payload = _load_single_json_object(normalized)
    except Exception as exc:
        raise ValueError(f"Failed to parse model response as JSON: {exc}")
    
    thought = payload.get("thought", "")
    action = payload.get("action")
    action_input = payload.get("action_input", {})
    if not isinstance(thought, str):
        raise ValueError("thought must be a string.")
    if not isinstance(action, str) or not action:
        raise ValueError("action must be a non-empty string.")
    if not isinstance(action_input, dict):
        raise ValueError("action_input must be a JSON object.")

    return ModelStep(
        thought=thought,
        action=action,
        action_input=action_input,
        raw_response=raw_response,
    )


# Intent-based Action Mapping for Strong Constraints
INTENT_TOOL_MAP = {
    "INSPECT_SCHEMA": ["get_data_schema", "inspect_sqlite_schema", "retrieve_schema_context"],
    "CHECK_VALUE_SPACE": ["execute_duckdb_sql", "execute_context_sql", "read_csv", "read_json"],
    "RELAX_FILTER": ["execute_duckdb_sql", "execute_context_sql"],
    "VERIFY_JOIN": ["execute_duckdb_sql", "execute_context_sql", "get_data_schema", "inspect_sqlite_schema"],
    "REWRITE_SQL": ["execute_duckdb_sql", "execute_context_sql"],
    "LIST_CONTEXT": ["list_context"],
    "READ_DOC": ["read_doc", "search_knowledge_doc", "list_knowledge_headings", "read_knowledge_section"],
    "GET_DATA_SCHEMA": ["get_data_schema"],
    "INSPECT_SQLITE_SCHEMA": ["inspect_sqlite_schema"],
    "EXECUTE_PYTHON": ["execute_python"],
    "CONTINUE": ["execute_duckdb_sql", "execute_context_sql", "execute_python", "get_data_schema", "inspect_sqlite_schema", "list_context", "read_csv", "read_doc", "read_json", "search_knowledge_doc"],
    "ANSWER": ["answer"],
    "FURTHER_ANALYSIS": ["execute_duckdb_sql", "execute_context_sql", "execute_python", "extract_dataframe_stats"]
}


class ReActAgent:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: ReActAgentConfig | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or ReActAgentConfig()
        self.system_prompt = system_prompt or REACT_SYSTEM_PROMPT

    def _build_messages(self, task: PublicTask, state: AgentRuntimeState) -> list[ModelMessage]:
        system_content = build_system_prompt(
            self.tools.describe_for_prompt(),
            system_prompt=self.system_prompt,
        )
        messages = [ModelMessage(role="system", content=system_content)]
        
        # Initial user message with task and context overview
        task_prompt = build_task_prompt(task)
        
        # Add context overview to the first prompt to prevent "blind querying"
        try:
            from data_agent_baseline.tools.filesystem import list_context_tree
            context_tree = list_context_tree(task, max_depth=2)
            files = [e["path"] for e in context_tree.get("entries", []) if e["kind"] == "file"]
            if files:
                task_prompt += f"\n\nAvailable files in context/:\n- " + "\n- ".join(files)
        except Exception:
            pass
            
        messages.append(ModelMessage(role="user", content=task_prompt))
        
        for step in state.steps:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(
                ModelMessage(role="user", content=build_observation_prompt(step.observation))
            )
        return messages

    def run(self, task: PublicTask) -> AgentRunResult:
        state = AgentRuntimeState()
        for step_index in range(1, self.config.max_steps + 1):
            llm_started_at = perf_counter()
            raw_response = self.model.complete(self._build_messages(task, state))
            llm_elapsed = round(perf_counter() - llm_started_at, 6)
            try:
                model_step = parse_model_step(raw_response)
                
                # Strong Constraint Validation (Orchestrator Level)
                if state.steps:
                    last_obs = state.steps[-1].observation
                    if isinstance(last_obs, dict) and "content" in last_obs:
                        content = last_obs["content"]
                        if isinstance(content, dict):
                            # 1. Blocked Operations Check (capability hard block)
                            blocked_ops = content.get("blocked_operations", [])
                            blocked_tool_map = {
                                "READ_SQLITE": {"execute_context_sql", "inspect_sqlite_schema"},
                                "READ_EXCEL": {"execute_duckdb_sql"},
                            }
                            blocked_tools = set()
                            for op in blocked_ops:
                                blocked_tools.update(blocked_tool_map.get(str(op).upper(), set()))
                            if model_step.action in blocked_tools:
                                raise ValueError(
                                    f"Action '{model_step.action}' is BLOCKED by the system. "
                                    f"Reason: {content.get('error_message', 'Capability missing')}."
                                )

                            # 2. Forbidden Actions Check (state hard block)
                            forbidden_actions = content.get("forbidden_actions", [])
                            if model_step.action.upper() in [a.upper() for a in forbidden_actions]:
                                raise ValueError(f"Action '{model_step.action}' is FORBIDDEN in the current state (e.g., cannot submit answer if result is empty).")

                tool_started_at = perf_counter()
                tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
                tool_elapsed = round(perf_counter() - tool_started_at, 6)
                truncated = False
                if isinstance(tool_result.content, dict):
                    truncated = bool(tool_result.content.get("truncated", False))
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
                    "meta": {
                        "llm_elapsed_seconds": llm_elapsed,
                        "tool_elapsed_seconds": tool_elapsed,
                        "truncated": truncated,
                    },
                }
                step_record = StepRecord(
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
                state.steps.append(step_record)
                if tool_result.is_terminal:
                    state.answer = tool_result.answer
                    break
            except Exception as exc:
                error_msg = str(exc)
                if "Invalid control character" in error_msg:
                    error_msg += " - DO NOT use literal newlines inside JSON strings. Use \\n instead."
                
                observation = {
                    "ok": False,
                    "error": error_msg,
                    "meta": {
                        "llm_elapsed_seconds": llm_elapsed,
                    },
                }
                state.steps.append(
                    StepRecord(
                        step_index=step_index,
                        thought="",
                        action="__error__",
                        action_input={},
                        raw_response=raw_response,
                        observation=observation,
                        ok=False,
                        llm_elapsed_seconds=llm_elapsed,
                    )
                )

        if state.answer is None and state.failure_reason is None:
            state.failure_reason = "Agent did not submit an answer within max_steps."

        return AgentRunResult(
            task_id=task.task_id,
            answer=state.answer,
            steps=list(state.steps),
            failure_reason=state.failure_reason,
        )
