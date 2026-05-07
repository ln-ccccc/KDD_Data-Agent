from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from data_agent_baseline.agents.model import ModelMessage
from data_agent_baseline.agents.prompt import build_observation_prompt, build_system_prompt, build_task_prompt
from data_agent_baseline.runtime.kernel.state import RuntimeState


@dataclass(frozen=True, slots=True)
class RenderConfig:
    max_recall_snippets: int = 3


class ContextRenderer:
    """Pure rendering: state -> messages (no side effects)."""

    def __init__(self, *, config: RenderConfig | None = None) -> None:
        self.config = config or RenderConfig()

    def build_messages(
        self, *, task: Any, state: RuntimeState, system_prompt: str, tools_description: str
    ) -> list[ModelMessage]:
        system_content = build_system_prompt(tools_description, system_prompt=system_prompt)
        
        plan_text = getattr(state, "current_plan", "")
        if plan_text:
            system_content += f"\n\n=== CURRENT PLAN & PROGRESS ===\n{plan_text}\n===============================\n"
        else:
            system_content += "\n\n=== CURRENT PLAN & PROGRESS ===\nNo plan created yet. Please use the `update_plan` tool to create one.\n===============================\n"

        messages = [ModelMessage(role="system", content=system_content)]

        user_task = build_task_prompt(task)
        abs_dict = state.abstract.model_dump(mode="python")
        if abs_dict:
            user_task += "\n\nWorking abstract (structured):\n" + json.dumps(abs_dict, ensure_ascii=False, indent=2)

        if state.recall_snippets:
            user_task += "\n\nRecall snippets (auto):\n" + json.dumps(
                state.recall_snippets[-self.config.max_recall_snippets :], ensure_ascii=False, indent=2
            )

        messages.append(ModelMessage(role="user", content=user_task))

        window_n = max(int(state.prompt_window_steps), 1)
        for step in state.steps[-window_n:]:
            if step.raw_response:
                messages.append(ModelMessage(role="assistant", content=step.raw_response))
            if step.observation:
                messages.append(ModelMessage(role="user", content=build_observation_prompt(step.observation)))
        return messages
