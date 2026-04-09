from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data_agent_baseline.runtime.kernel.state import RuntimeState


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    max_prompt_chars: int = 24_000
    compress_trigger_steps: int = 12
    default_window_steps: int = 8
    min_window_steps: int = 2

    # Abstract update control (sparse trigger + cooldown/backoff)
    abstract_min_interval_steps: int = 6
    abstract_failure_backoff_steps: int = 8

    # Recall control
    recall_cooldown_steps: int = 2


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    window_steps: int
    should_update_abstract: bool
    should_inject_auto_recall: bool
    auto_recall_request: dict[str, Any] | None


class ContextPolicy:
    """Decide context-control actions from state + cheap estimates (no side effects)."""

    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def decide(self, *, prompt_chars_estimate: int, state: RuntimeState) -> PolicyDecision:
        # Window sizing: shrink if oversized.
        window = max(int(state.prompt_window_steps or self.config.default_window_steps), self.config.min_window_steps)
        if prompt_chars_estimate > self.config.max_prompt_chars:
            # shrink aggressively, but bounded
            while window > self.config.min_window_steps and prompt_chars_estimate > self.config.max_prompt_chars:
                window -= 1
                # prompt_chars_estimate will be recomputed by orchestrator/renderer.
                break

        # Abstract update: sparse triggers only.
        should_update = False
        if state.steps and (
            state.steps_since_compress >= self.config.compress_trigger_steps
            or prompt_chars_estimate > self.config.max_prompt_chars
        ):
            # cooldown/backoff (stored in extra fields for now)
            last_update_step = int(getattr(state, "abstract_last_update_step", -10_000))
            if len(state.steps) - last_update_step >= self.config.abstract_min_interval_steps:
                backoff_until = int(getattr(state, "abstract_backoff_until_step", -1))
                if len(state.steps) >= backoff_until:
                    should_update = True

        # Auto recall: let reducer schedule via pending_auto_recall, policy just decides whether to inject.
        should_recall = False
        req = state.pending_auto_recall if isinstance(state.pending_auto_recall, dict) else None
        if req and state.recall_cooldown_steps <= 0:
            should_recall = True

        return PolicyDecision(
            window_steps=window,
            should_update_abstract=should_update,
            should_inject_auto_recall=should_recall,
            auto_recall_request=req if should_recall else None,
        )

