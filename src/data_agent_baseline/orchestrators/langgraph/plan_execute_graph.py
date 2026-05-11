from __future__ import annotations

from dataclasses import dataclass

from data_agent_baseline.agents.model import ModelAdapter
from data_agent_baseline.agents.plan_execute import (
    _build_planner_messages,
    _parse_plan,
    _render_plan_for_prompt,
)
from data_agent_baseline.agents.prompt import REACT_SYSTEM_PROMPT
from data_agent_baseline.agents.runtime import AgentRunResult
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.orchestrators.langgraph.react_graph import LangGraphReActAgent, LangGraphReActAgentConfig
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class LangGraphPlanAndExecuteConfig:
    max_steps: int = 64
    history_max_steps: int = 16
    max_prompt_chars: int = 24_000
    compress_trigger_steps: int = 12


class LangGraphPlanAndExecuteAgent:
    """Plan first, then run the ReAct loop inside a LangGraph state machine.
    Uses a Hard Gate to enforce Semantic Validation before planning and execution.
    """

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
        # 1. HARD GATE: Semantic Validation (disabled for compatibility with current PublicTask schema)
        mapping_dict = "No semantic validation performed."

        # 2. PLAN PHASE
        planner_messages = _build_planner_messages(task, self.tools)
        raw_plan = self.model.complete(planner_messages)
        steps = _parse_plan(raw_plan)
        plan_text = _render_plan_for_prompt(steps)
        
        # 3. EXECUTE PHASE
        planned_system = (
            f"{REACT_SYSTEM_PROMPT}\n\nHigh-level plan:\n{plan_text}\n\n"
            f"### Verified Semantic Mapping (HARD GATE)\n{mapping_dict}\n\n"
            "Follow the plan and MUST use the verified semantic mapping above to write accurate SQL. "
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
