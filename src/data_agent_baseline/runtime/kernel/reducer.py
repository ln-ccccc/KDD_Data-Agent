from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from data_agent_baseline.runtime.context_pruning import prune_for_prompt
from data_agent_baseline.runtime.kernel.memory import schedule_auto_recall_after_sql_error
from data_agent_baseline.runtime.kernel.state import RuntimeState, RuntimeStep


@dataclass(frozen=True, slots=True)
class ReducerConfig:
    observation_max_string_chars: int = 4000
    observation_max_list_items: int = 50

    recall_cooldown_steps: int = 2


def classify_error(action: str, observation: dict[str, Any]) -> str | None:
    err = observation.get("error")
    if isinstance(err, dict):
        err_type = str(err.get("type", "")).strip() or "Error"
        msg = str(err.get("message", "")).lower()
    else:
        err_type = "Error"
        msg = str(err).lower()

    if not msg:
        return None
    if "no such table" in msg:
        pat = "no_such_table"
    elif "no such column" in msg:
        pat = "no_such_column"
    elif "syntax error" in msg:
        pat = "syntax_error"
    elif "timed out" in msg or "timeout" in msg:
        pat = "timeout"
    elif "invalid control character" in msg:
        pat = "invalid_json_string"
    else:
        pat = msg[:80]
    return f"{action}:{err_type}:{pat}"


class StateReducer:
    """The only place allowed to mutate RuntimeState (SSOT)."""

    def __init__(self, config: ReducerConfig | None = None) -> None:
        self.config = config or ReducerConfig()

    def record_llm(self, state: RuntimeState, *, raw_response: str, llm_elapsed: float) -> None:
        state.last_raw_response = raw_response
        state.last_llm_elapsed = llm_elapsed

    def record_step(
        self,
        state: RuntimeState,
        *,
        thought: str,
        action: str,
        action_input: dict[str, Any],
        raw_response: str,
        observation: dict[str, Any],
        ok: bool,
        llm_elapsed: float,
        tool_elapsed: float | None,
    ) -> None:
        # Centralized pruning before feeding back to LLM.
        if "content" in observation:
            pruned_content, truncated = prune_for_prompt(
                observation.get("content"),
                max_string_chars=self.config.observation_max_string_chars,
                max_list_items=self.config.observation_max_list_items,
            )
            observation["content"] = pruned_content
            meta = observation.get("meta")
            if not isinstance(meta, dict):
                meta = {}
                observation["meta"] = meta
            meta["truncated"] = bool(meta.get("truncated", False) or truncated)

        step = RuntimeStep(
            step_index=len(state.steps) + 1,
            thought=thought,
            action=action,
            action_input=action_input,
            raw_response=raw_response,
            observation=observation,
            ok=ok,
            llm_elapsed_seconds=llm_elapsed,
            tool_elapsed_seconds=tool_elapsed,
        )
        state.steps.append(step)
        state.steps_since_compress += 1

        # Tick down recall cooldown once per step.
        state.recall_cooldown_steps = max(int(state.recall_cooldown_steps) - 1, 0)

        # Error storm tracking (closed-loop signal).
        error_key = classify_error(action, observation)
        if not error_key:
            state.last_error_key = None
            state.error_streak = 0
            return

        if state.last_error_key == error_key:
            state.error_streak += 1
        else:
            state.last_error_key = error_key
            state.error_streak = 1

        # Inject a strategy hint at streak>=2 and schedule auto recall for SQL errors.
        if state.error_streak >= 2:
            meta = observation.get("meta")
            if not isinstance(meta, dict):
                meta = {}
                observation["meta"] = meta
            if action in {"execute_context_sql", "execute_duckdb_sql"}:
                path = action_input.get("path")
                meta["strategy_hint"] = {
                    "message": "Repeated SQL error. Inspect schema first, then retry with correct table/column names.",
                    "suggested_action": "inspect_sqlite_schema" if action == "execute_context_sql" else "get_data_schema",
                    "suggested_action_input": {"path": path} if isinstance(path, str) and path else {},
                }
                if action == "execute_context_sql":
                    schedule_auto_recall_after_sql_error(state, path=str(path) if isinstance(path, str) else None)
                    state.recall_cooldown_steps = max(state.recall_cooldown_steps, self.config.recall_cooldown_steps)
            elif action == "execute_python":
                meta["strategy_hint"] = {
                    "message": "Repeated Python error. Reduce output/print, validate column names, or prefer SQL/DuckDB aggregation.",
                    "suggested_action": "execute_duckdb_sql",
                    "suggested_action_input": {},
                }

