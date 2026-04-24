from __future__ import annotations

from dataclasses import dataclass

from data_agent_baseline.agents.model import ModelAdapter
from data_agent_baseline.agents.plan_execute import _parse_plan, _render_plan_for_prompt
from data_agent_baseline.agents.prompt import REACT_SYSTEM_PROMPT
from data_agent_baseline.agents.runtime import AgentRunResult
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.orchestrators.langgraph.react_graph import LangGraphReActAgent, LangGraphReActAgentConfig
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class LangGraphPlanAndExecuteConfig:
    max_steps: int = 16
    history_max_steps: int = 8
    max_prompt_chars: int = 24_000
    compress_trigger_steps: int = 12


class LangGraphPlanAndExecuteAgent:
    """Plan first, then run the ReAct loop inside a LangGraph state machine."""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: LangGraphPlanAndExecuteConfig | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or LangGraphPlanAndExecuteConfig()

    def run(self, task: PublicTask) -> AgentRunResult:
        prompt = (
            "You are the Planner for a Data Agent. "
            "Your job is to read the following task and generate a high-level step-by-step plan.\n\n"
            "CRITICAL RULES:\n"
            "1. ADJUDICATION FIRST: Your first step MUST ALWAYS be to use `delegate_to_data_analyst` to spawn a Semantic Validator subagent. This subagent must be instructed to read knowledge.md and probe the database to verify the exact categorical values, table names, and column names required for the task.\n"
            "2. DO NOT skip the subagent delegation. The subagent must return a precise Mapping Dictionary before any SQL is written.\n"
            "3. Only after receiving the Mapping Dictionary should you proceed to query data.\n\n"
            f"Question: {task.question}\n\n"
            "Output your plan as a numbered list."
        )
        raw_plan = self.model.complete(prompt)
        steps = _parse_plan(raw_plan)
        plan_text = _render_plan_for_prompt(steps)
        planned_system = (
            f"{REACT_SYSTEM_PROMPT}\n\nHigh-level plan:\n{plan_text}\n\n"
            "Follow the plan unless observations require adjustments. "
            "Use tools to gather evidence and then submit the final answer."
        )
        react = LangGraphReActAgent(
            model=self.model,
            tools=self.tools,
            config=LangGraphReActAgentConfig(
                max_steps=self.config.max_steps,
                history_max_steps=self.config.history_max_steps,
                max_prompt_chars=self.config.max_prompt_chars,
                compress_trigger_steps=self.config.compress_trigger_steps,
            ),
            system_prompt=planned_system,
        )
        return react.run(task)
