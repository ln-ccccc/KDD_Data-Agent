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
    max_steps: int = 64
    history_max_steps: int = 16
    max_prompt_tokens: int = 6_000
    compress_trigger_steps: int = 12
    task_timeout_seconds: float = 0.0


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
        # 1. HARD GATE: Semantic Validation (Subagent)
        mapping_dict = "No semantic validation performed."
        if "delegate_to_data_analyst" in self.tools.handlers:
            import json
            print(f"--- [HARD GATE] Launching Semantic Validator for Task {task.id} ---")
            subagent_tool = self.tools.handlers["delegate_to_data_analyst"]
            sub_input = {
                "sub_task_description": f"Analyze the following question and probe the database/knowledge base to find exact table names, column names, and categorical values required to answer it. Question: {task.question}",
                "focus_files": []
            }
            subagent_result = subagent_tool(task, sub_input)
            if subagent_result.ok:
                mapping_dict = json.dumps(subagent_result.content, ensure_ascii=False, indent=2)
            else:
                mapping_dict = f"Semantic Validator failed or returned partial results: {subagent_result.content}"
            print(f"--- [HARD GATE] Semantic Validator Finished ---")

        # 2. PLAN PHASE
        prompt = (
            "You are the Planner for a Data Agent. "
            "Your job is to read the following task and generate a high-level step-by-step plan.\n\n"
            "We have already executed a Semantic Validator subagent to probe the data and knowledge base. "
            f"Here are the exact findings from the Semantic Validator (HARD GATE):\n{mapping_dict}\n\n"
            "Use these verified findings to plan the SQL execution steps. "
            "Do NOT plan to use `delegate_to_data_analyst` again, as it has already been executed.\n"
            f"Question: {task.question}\n\n"
            "Output your plan as a numbered list."
        )
        raw_plan = self.model.complete(prompt)
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
                max_prompt_tokens=self.config.max_prompt_tokens,
                compress_trigger_steps=self.config.compress_trigger_steps,
                task_timeout_seconds=self.config.task_timeout_seconds,
            ),
            system_prompt=planned_system,
        )
        return react.run(task)
