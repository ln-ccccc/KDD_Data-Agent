from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.agents.prompt import REACT_SYSTEM_PROMPT, build_system_prompt, build_task_prompt
from data_agent_baseline.agents.react import ReActAgent, ReActAgentConfig
from data_agent_baseline.agents.runtime import AgentRunResult
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


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
    payload, end = json.JSONDecoder().raw_decode(text)
    remainder = text[end:].strip()
    if remainder:
        cleaned_remainder = re.sub(r"(?:\\[nrt])+", "", remainder).strip()
        if cleaned_remainder:
            raise ValueError("Model response must contain only one JSON object.")
    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")
    return payload


@dataclass(frozen=True, slots=True)
class PlanStep:
    description: str
    action: str | None = None
    action_input: dict[str, Any] | None = None


PLANNER_SYSTEM_PROMPT = """
You are a planning module for a data agent.
Given a question and available tools, produce a concise high-level plan to solve the task.
Return exactly one JSON object with key "plan" as a list of steps.
Each step is an object with:
- "description": a short imperative sentence
- optional "action": one of the available tool names when you know which tool to use
- optional "action_input": a JSON object with the arguments for that action if specified
Do not include any text before or after the fenced JSON block.
""".strip()


def _build_planner_messages(task: PublicTask, tools: ToolRegistry) -> list[ModelMessage]:
    tool_desc = tools.describe_for_prompt()
    system = f"{PLANNER_SYSTEM_PROMPT}\n\nAvailable tools:\n{tool_desc}"
    user = f"Question: {task.question}\nReturn a plan now."
    return [ModelMessage(role="system", content=system), ModelMessage(role="user", content=user)]


def _parse_plan(raw_response: str) -> list[PlanStep]:
    text = _strip_json_fence(raw_response)
    obj = _load_single_json_object(text)
    plan = obj.get("plan")
    if not isinstance(plan, list) or not plan:
        raise ValueError("plan must be a non-empty list.")
    steps: list[PlanStep] = []
    for item in plan:
        if not isinstance(item, dict):
            raise ValueError("Each plan step must be an object.")
        desc = item.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise ValueError("Each plan step must have a non-empty description.")
        action = item.get("action")
        action_input = item.get("action_input")
        if action is not None and not isinstance(action, str):
            raise ValueError("action must be a string when provided.")
        if action_input is not None and not isinstance(action_input, dict):
            raise ValueError("action_input must be a JSON object when provided.")
        steps.append(PlanStep(description=desc.strip(), action=action, action_input=action_input))
    return steps


def _render_plan_for_prompt(steps: list[PlanStep]) -> str:
    lines = []
    for i, step in enumerate(steps, start=1):
        if step.action and step.action_input is not None:
            lines.append(f"{i}. {step.description} [action={step.action}]")
        else:
            lines.append(f"{i}. {step.description}")
    return "\n".join(lines)


class PlanAndExecuteAgent:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: ReActAgentConfig | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or ReActAgentConfig()

    def run(self, task: PublicTask) -> AgentRunResult:
        planner_messages = _build_planner_messages(task, self.tools)
        raw_plan = self.model.complete(planner_messages)
        steps = _parse_plan(raw_plan)
        plan_text = _render_plan_for_prompt(steps)
        planned_system = (
            f"{REACT_SYSTEM_PROMPT}\n\nHigh-level plan:\n{plan_text}\n\n"
            "Follow the plan unless observations require adjustments. "
            "Use tools to gather evidence and then submit the final answer."
        )
        react = ReActAgent(
            model=self.model,
            tools=self.tools,
            config=self.config,
            system_prompt=planned_system,
        )
        return react.run(task)

