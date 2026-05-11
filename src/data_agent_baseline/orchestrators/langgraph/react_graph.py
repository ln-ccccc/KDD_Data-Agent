from __future__ import annotations

import json
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
from data_agent_baseline.runtime.kernel.memory import AbstractUpdater, AbstractUpdateConfig, estimate_prompt_chars
from data_agent_baseline.runtime.kernel.policy import ContextPolicy, PolicyConfig
from data_agent_baseline.runtime.kernel.recall_provider import RecallProvider
from data_agent_baseline.runtime.kernel.reducer import ReducerConfig, StateReducer
from data_agent_baseline.runtime.kernel.renderer import ContextRenderer, RenderConfig
from data_agent_baseline.runtime.kernel.state import AbstractMemory, RuntimeState, coerce_state
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class LangGraphReActAgentConfig:
    max_steps: int = 64
    history_max_steps: int = 16  # default prompt window
    observation_max_string_chars: int = 4000
    observation_max_list_items: int = 50
    parse_repair_max_retries: int = 1
    # Context control (policy knobs)
    max_prompt_chars: int = 24_000
    compress_trigger_steps: int = 12
    min_prompt_window_steps: int = 2
    abstract_min_interval_steps: int = 6
    abstract_failure_backoff_steps: int = 8
    recall_cooldown_steps: int = 2


class _GraphState(TypedDict, total=False):
    task: PublicTask
    system_prompt: str
    stop_reason: str
    rt: dict[str, Any]  # RuntimeState (SSOT) dumped as dict


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
        # Kernel components (policy / rendering / reducers).
        self._policy = ContextPolicy(
            PolicyConfig(
                max_prompt_chars=self.config.max_prompt_chars,
                compress_trigger_steps=self.config.compress_trigger_steps,
                default_window_steps=self.config.history_max_steps,
                min_window_steps=self.config.min_prompt_window_steps,
                abstract_min_interval_steps=self.config.abstract_min_interval_steps,
                abstract_failure_backoff_steps=self.config.abstract_failure_backoff_steps,
                recall_cooldown_steps=self.config.recall_cooldown_steps,
            )
        )
        self._reducer = StateReducer(
            ReducerConfig(
                observation_max_string_chars=self.config.observation_max_string_chars,
                observation_max_list_items=self.config.observation_max_list_items,
                recall_cooldown_steps=self.config.recall_cooldown_steps,
            )
        )
        self._abstract_updater = AbstractUpdater(
            model=self.model,
            config=AbstractUpdateConfig(
                observation_max_string_chars=self.config.observation_max_string_chars,
                observation_max_list_items=self.config.observation_max_list_items,
            ),
        )
        self._recall_provider = RecallProvider()
        self._renderer = ContextRenderer(
            tools_description=self.tools.describe_for_prompt(),
            config=RenderConfig(max_recall_snippets=3),
        )
        self._app = self._build_graph()

    def _repair_action_json(self, *, raw_response: str, error: str) -> str:
        """Ask the model to reformat its last output into valid action JSON."""
        prompt = (
            "Your previous response could not be parsed as the required JSON action object.\n"
            f"Parse error: {error}\n\n"
            "Return exactly ONE ```json fenced block containing ONE JSON object with keys:\n"
            "- thought: string\n"
            "- action: string\n"
            "- action_input: object\n"
            "No extra text before/after.\n\n"
            "Previous response:\n"
            f"{raw_response}\n"
        )
        msgs = [ModelMessage(role="system", content=REACT_SYSTEM_PROMPT), ModelMessage(role="user", content=prompt)]
        return self.model.complete(msgs)

    def _llm_step(self, gs: _GraphState) -> _GraphState:
        task = gs["task"]
        system_prompt = gs.get("system_prompt") or self.system_prompt
        rt = coerce_state(gs.get("rt") or {})

        # Policy: estimate prompt size using current window.
        window_n = max(int(rt.prompt_window_steps), 1)
        step_dicts = [s.model_dump(mode="python") for s in rt.steps[-window_n:]]
        prompt_chars = estimate_prompt_chars(
            question=getattr(task, "question", ""),
            abstract=rt.abstract.model_dump(mode="python"),
            steps=step_dicts,
        )
        decision = self._policy.decide(prompt_chars_estimate=prompt_chars, state=rt)
        rt.prompt_window_steps = decision.window_steps

        # Mechanism: auto recall injection (internal memory read)
        if decision.should_inject_auto_recall and decision.auto_recall_request is not None:
            recall_payload = self._recall_provider.recall(state=rt, request=decision.auto_recall_request)
            rt.recall_snippets.append(
                {
                    "why": "auto_recall_after_repeated_error",
                    "request": decision.auto_recall_request,
                    "content": recall_payload,
                }
            )
            rt.pending_auto_recall = None
            rt.recall_cooldown_steps = self.config.recall_cooldown_steps

        # Mechanism: abstract update (sparse + soft-fail)
        if decision.should_update_abstract:
            older_steps = [s.model_dump(mode="python") for s in rt.steps[: -rt.prompt_window_steps]]
            new_abs = self._abstract_updater.update(
                question=getattr(task, "question", ""),
                current=rt.abstract,
                older_steps=older_steps,
            )
            if new_abs is not None:
                rt.abstract = new_abs
                rt.steps_since_compress = 0
                # record last update step (stored as extra for policy)
                rt.__dict__["abstract_last_update_step"] = len(rt.steps)  # pydantic extra
            else:
                # backoff on failure
                rt.__dict__["abstract_backoff_until_step"] = len(rt.steps) + self.config.abstract_failure_backoff_steps

        llm_started_at = perf_counter()
        raw_response = self.model.complete(
            self._renderer.build_messages(task=task, state=rt, system_prompt=system_prompt)
        )
        llm_elapsed = round(perf_counter() - llm_started_at, 6)

        self._reducer.record_llm(rt, raw_response=raw_response, llm_elapsed=llm_elapsed)
        gs["rt"] = rt.to_langgraph_dict()
        return gs

    def _tool_step(self, gs: _GraphState) -> _GraphState:
        task = gs["task"]
        rt = coerce_state(gs.get("rt") or {})
        raw_response = str(rt.last_raw_response or "")
        llm_elapsed = float(rt.last_llm_elapsed or 0.0)

        try:
            last_parse_error: str | None = None
            model_step = None
            current_raw = raw_response
            for attempt in range(self.config.parse_repair_max_retries + 1):
                try:
                    model_step = parse_model_step(current_raw)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_parse_error = str(exc)
                    if attempt >= self.config.parse_repair_max_retries:
                        raise
                    current_raw = self._repair_action_json(raw_response=current_raw, error=last_parse_error)

            assert model_step is not None  # for type-checkers

            # Strong Constraint Validation (Orchestrator Level)
            from data_agent_baseline.agents.react import INTENT_TOOL_MAP
            if rt.steps:
                last_step = rt.steps[-1]
                last_obs = last_step.observation
                if isinstance(last_obs, dict) and "content" in last_obs:
                    content = last_obs["content"]
                    if isinstance(content, dict):
                        # 1. Blocked Operations Check
                        blocked_ops = content.get("blocked_operations", [])
                        if model_step.action.upper() in [op.upper() for op in blocked_ops]:
                            raise ValueError(f"Action '{model_step.action}' is BLOCKED by the system. Reason: {content.get('error_message', 'Capability missing')}. Choose an alternative path.")
                        
                        # 2. Allowed Next Actions Check
                        allowed_intents = content.get("allowed_next_actions", [])
                        if allowed_intents:
                            allowed_tools = []
                            for intent in allowed_intents:
                                allowed_tools.extend(INTENT_TOOL_MAP.get(intent.upper(), []))
                            
                            if model_step.action not in allowed_tools:
                                raise ValueError(f"Action '{model_step.action}' is NOT allowed at this state. You MUST choose one of the following intents: {allowed_intents}")

                        # 3. Forbidden Actions Check
                        forbidden_actions = content.get("forbidden_actions", [])
                        if model_step.action.upper() in [a.upper() for a in forbidden_actions]:
                            raise ValueError(f"Action '{model_step.action}' is FORBIDDEN in the current state.")

            # Recall is an internal memory read, not a tool. If the model tries to call it,
            # return a structured error and keep going.
            if model_step.action == "recall":
                observation = {
                    "ok": False,
                    "tool": "recall",
                    "error": {
                        "type": "RecallIsInternal",
                        "message": "Recall is handled automatically by the runtime. Do NOT call recall as a tool. Continue with normal tools.",
                    },
                    "meta": {"llm_elapsed_seconds": llm_elapsed},
                }
                self._reducer.record_step(
                    rt,
                    thought=model_step.thought,
                    action="recall",
                    action_input=model_step.action_input,
                    raw_response=current_raw,
                    observation=observation,
                    ok=False,
                    llm_elapsed=llm_elapsed,
                    tool_elapsed=None,
                )
                gs["rt"] = rt.to_langgraph_dict()
                return gs

            tool_started_at = perf_counter()
            tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
            tool_elapsed = round(perf_counter() - tool_started_at, 6)

            # Intercept plan updates to write to RuntimeState
            if model_step.action == "update_plan":
                rt.current_plan = model_step.action_input.get("new_plan", rt.current_plan)

            observation: dict[str, Any] = {
                "ok": tool_result.ok,
                "tool": model_step.action,
                "content": tool_result.content,
                "meta": {
                    "llm_elapsed_seconds": llm_elapsed,
                    "tool_elapsed_seconds": tool_elapsed,
                },
            }
            self._reducer.record_step(
                rt,
                thought=model_step.thought,
                action=model_step.action,
                action_input=model_step.action_input,
                raw_response=current_raw,
                observation=observation,
                ok=tool_result.ok,
                llm_elapsed=llm_elapsed,
                tool_elapsed=tool_elapsed,
            )
            gs["rt"] = rt.to_langgraph_dict()

            if tool_result.is_terminal:
                # Store answer as serializable dict to pending_answer for review
                answer = tool_result.answer.to_dict() if tool_result.answer is not None else None
                rt.pending_answer = answer
                gs["stop_reason"] = "reviewing_answer"
                gs["rt"] = rt.to_langgraph_dict()
                return gs

        except Exception as exc:  # noqa: BLE001
            import traceback
            error_msg = str(exc)
            error_type = type(exc).__name__
            full_traceback = traceback.format_exc()
            if "Invalid control character" in error_msg:
                error_msg += " - DO NOT use literal newlines inside JSON strings. Use \\n instead."

            observation = {
                "ok": False,
                "error": {
                    "type": error_type,
                    "message": error_msg,
                    "traceback": full_traceback,
                },
                "meta": {"llm_elapsed_seconds": llm_elapsed},
            }
            self._reducer.record_step(
                rt,
                thought="",
                action="__error__",
                action_input={},
                raw_response=raw_response,
                observation=observation,
                ok=False,
                llm_elapsed=llm_elapsed,
                tool_elapsed=None,
            )
            gs["rt"] = rt.to_langgraph_dict()

        if len(rt.steps) >= self.config.max_steps:
            rt.failure_reason = "Agent did not submit an answer within max_steps."
            gs["stop_reason"] = "max_steps"
            gs["rt"] = rt.to_langgraph_dict()
        return gs

    def _reflection_step(self, gs: _GraphState) -> _GraphState:
        rt = coerce_state(gs.get("rt") or {})
        if not rt.steps:
            return gs

        last_step = rt.steps[-1]
        
        # Determine if we need to reflect based on tool execution outcome
        needs_reflection = False
        reflection_hint = ""
        
        # 1. Hard Errors (Exception or Tool failed)
        if not last_step.ok and isinstance(last_step.observation, dict):
            error_info = last_step.observation.get("error")
            content = last_step.observation.get("content", {})
            if isinstance(content, dict) and not error_info:
                if "error" in content:
                    error_info = content.get("error")

            if error_info:
                needs_reflection = True
                error_type = error_info.get("type", "UnknownError") if isinstance(error_info, dict) else "Error"
                error_msg = error_info.get("message", str(error_info)) if isinstance(error_info, dict) else str(error_info)
                reflection_hint = (
                    f"REFLECTION: The tool '{last_step.action}' failed with {error_type}. "
                    f"Error details: {error_msg}. "
                    "Analyze the error message and traceback to understand what went wrong. "
                    "If it is a schema mismatch or syntax error, use inspection tools like `retrieve_schema_context` "
                    "or `get_data_schema` to verify your assumptions before retrying. "
                    "If you find a fix, remember to use `update_cheat_sheet`."
                )
                
        # 2. Soft Errors (Execution succeeded but returned 0 rows or warnings)
        elif last_step.ok and isinstance(last_step.observation, dict):
            content = last_step.observation.get("content", {})
            if isinstance(content, dict) and "warning" in content:
                needs_reflection = True
                # Override the 'ok' status so the LLM pays attention to the failure
                last_step.ok = False
                warning_msg = content.get("warning", "")
                reflection_hint = (
                    f"REFLECTION (Data Anomaly): {warning_msg} "
                    "You MUST NOT submit this empty result as your final answer. "
                    "Reflect on your query logic, remove overly strict WHERE clauses, or search the Schema Catalog for the correct spelling."
                )

        if needs_reflection:
            if not isinstance(last_step.observation, dict):
                last_step.observation = {"original_content": last_step.observation}
            last_step.observation["reflection_hint"] = reflection_hint
        gs["rt"] = rt.to_langgraph_dict()
        return gs

    def _answer_review_step(self, gs: _GraphState) -> _GraphState:
        rt = coerce_state(gs.get("rt") or {})
        pending_answer = getattr(rt, "pending_answer", None)
        if not pending_answer:
            return gs
            
        task = gs["task"]
        from data_agent_baseline.agents.prompt import ANSWER_REVIEW_SYSTEM_PROMPT, build_answer_review_prompt
        
        msgs = [
            ModelMessage(role="system", content=ANSWER_REVIEW_SYSTEM_PROMPT),
            ModelMessage(role="user", content=build_answer_review_prompt(task.question, pending_answer))
        ]
        
        try:
            review_response = self.model.complete(msgs)
            import re
            match = re.search(r'```json\s*(.*?)\s*```', review_response, re.DOTALL)
            if match:
                review_response = match.group(1)
            review_data = json.loads(review_response)
            status = review_data.get("status", "REJECT")
            reason = review_data.get("reason", "Failed to parse review reason.")
        except Exception as e:
            # Fallback to PASS if reviewer logic crashes to avoid infinite loop
            status = "PASS"
            reason = str(e)
            
        if status == "PASS":
            rt.answer = pending_answer
            gs["stop_reason"] = "submitted_answer"
        else:
            # Reject: Mark the last step as failed and provide feedback
            if rt.steps:
                last_step = rt.steps[-1]
                last_step.ok = False
                last_step.observation = {
                    "ok": False,
                    "error": f"QA REVIEW REJECTED YOUR ANSWER: {reason}. Please continue reasoning, fix the issue, and submit a new answer."
                }
                
        rt.pending_answer = None
        gs["rt"] = rt.to_langgraph_dict()
        return gs

    def _should_continue(self, gs: _GraphState) -> str:
        rt = coerce_state(gs.get("rt") or {})
        if rt.answer is not None:
            return "end"
        if getattr(rt, "pending_answer", None) is not None:
            return "review"
        if rt.failure_reason is not None:
            return "end"
        if len(rt.steps) >= self.config.max_steps:
            return "end"
        if rt.steps and not rt.steps[-1].ok:
            return "reflect"
        return "continue"

    def _review_should_continue(self, gs: _GraphState) -> str:
        rt = coerce_state(gs.get("rt") or {})
        if rt.answer is not None:
            return "end"
        return "reflect"  # Route rejected answer to reflection for further hints

    def _build_graph(self):
        g = StateGraph(_GraphState)
        g.add_node("llm", self._llm_step)
        g.add_node("tool", self._tool_step)
        g.add_node("reflection", self._reflection_step)
        g.add_node("answer_review", self._answer_review_step)
        g.set_entry_point("llm")
        g.add_edge("llm", "tool")
        g.add_conditional_edges("tool", self._should_continue, {"continue": "llm", "reflect": "reflection", "review": "answer_review", "end": END})
        g.add_conditional_edges("answer_review", self._review_should_continue, {"end": END, "reflect": "reflection"})
        g.add_edge("reflection", "llm")
        return g.compile()

    def run(self, task: PublicTask) -> AgentRunResult:
        init_rt = RuntimeState(
            abstract=AbstractMemory(),
            prompt_window_steps=self.config.history_max_steps,
            steps_since_compress=0,
        )
        result = self._app.invoke({"task": task, "system_prompt": self.system_prompt, "rt": init_rt.to_langgraph_dict()})

        rt = coerce_state(result.get("rt") or {})
        runtime_state = AgentRuntimeState()
        for step in rt.steps:
            runtime_state.steps.append(
                StepRecord(
                    step_index=step.step_index,
                    thought=step.thought,
                    action=step.action,
                    action_input=dict(step.action_input or {}),
                    raw_response=step.raw_response,
                    observation=dict(step.observation or {}),
                    ok=bool(step.ok),
                    llm_elapsed_seconds=step.llm_elapsed_seconds,
                    tool_elapsed_seconds=step.tool_elapsed_seconds,
                )
            )

        answer_dict = rt.answer
        if isinstance(answer_dict, dict) and answer_dict.get("columns") is not None:
            from data_agent_baseline.benchmark.schema import AnswerTable  # local import

            runtime_state.answer = AnswerTable(
                columns=list(answer_dict.get("columns", [])),
                rows=[list(row) for row in answer_dict.get("rows", [])],
            )
        runtime_state.failure_reason = rt.failure_reason
        if runtime_state.answer is None and not runtime_state.failure_reason:
            runtime_state.failure_reason = "Agent stopped without an answer."

        return AgentRunResult(
            task_id=task.task_id,
            answer=runtime_state.answer,
            steps=list(runtime_state.steps),
            failure_reason=runtime_state.failure_reason,
        )
