from __future__ import annotations

"""DEPRECATED: ContextCompressionMiddleware.

This implementation was superseded by the runtime kernel:
- ContextPolicy decides when/what to compress
- AbstractUpdater performs LLM-based abstract updates
- ContextRenderer builds messages with bounded windows
- StateReducer applies pruning and updates SSOT state

Kept for historical reference only; not used by current graph runtimes.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.runtime.context_pruning import prune_for_prompt


@dataclass(frozen=True, slots=True)
class ContextCompressionConfig:
    # Triggering
    max_prompt_chars: int = 24_000
    max_steps_since_compress: int = 12

    # Prompt window control
    default_window_steps: int = 8
    min_window_steps: int = 2

    # Abstract limits
    max_files: int = 50
    max_tables: int = 50
    max_facts: int = 30
    max_open_questions: int = 10

    # Pruning before feeding to LLM
    observation_max_string_chars: int = 4000
    observation_max_list_items: int = 50


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:  # noqa: BLE001
        return str(obj)


_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
_GENERIC_FENCE_RE = re.compile(r"```\s*(.*?)\s*```", re.DOTALL)


def _strip_fence(text: str) -> str:
    candidate = text.strip()
    m = _JSON_FENCE_RE.search(candidate)
    if m:
        return m.group(1).strip()
    m = _GENERIC_FENCE_RE.search(candidate)
    if m:
        return m.group(1).strip()
    return candidate


def _estimate_prompt_chars(state: dict[str, Any], *, window_steps: int) -> int:
    """Very cheap prompt size estimate using character counts."""
    task = state.get("task")
    question = getattr(task, "question", "") if task is not None else ""

    abstract = state.get("abstract") or {}
    steps = state.get("steps") or []
    if not isinstance(steps, list):
        steps = []

    recent = steps[-window_steps:] if window_steps > 0 else []
    size = len(question) + len(_safe_json(abstract))
    for step in recent:
        if not isinstance(step, dict):
            continue
        size += len(str(step.get("raw_response", "")))
        size += len(_safe_json(step.get("observation", {})))
    return size


def _merge_unique(existing: list[str], new: list[str], *, limit: int) -> list[str]:
    seen = set(existing)
    merged = list(existing)
    for item in new:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    if limit > 0:
        merged = merged[-limit:]
    return merged


class ContextCompressionMiddleware:
    """Maintain a bounded working context: abstract + recent steps window.

    - Trigger: estimated prompt chars > max_prompt_chars OR steps since last compress >= threshold
    - Effect: updates `state["abstract"]` (structured) and suggests a smaller prompt window if needed.

    Note: We do NOT delete steps (archive remains in trace). We only control what goes into the prompt.
    """

    def __init__(self, *, model: ModelAdapter, config: ContextCompressionConfig | None = None) -> None:
        self.model = model
        self.config = config or ContextCompressionConfig()

    def before_llm(self, state: dict[str, Any]) -> dict[str, Any]:
        # Ensure defaults
        if "abstract" not in state or not isinstance(state.get("abstract"), dict):
            state["abstract"] = {
                "assets": {"files": [], "tables": []},
                "facts": [],
                "open_questions": [],
            }
        if "prompt_window_steps" not in state:
            state["prompt_window_steps"] = self.config.default_window_steps
        if "steps_since_compress" not in state:
            state["steps_since_compress"] = 0

        steps = state.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return state

        # Step-based trigger
        state["steps_since_compress"] = int(state.get("steps_since_compress", 0))
        trigger_by_steps = state["steps_since_compress"] >= self.config.max_steps_since_compress

        window = int(state.get("prompt_window_steps", self.config.default_window_steps))
        window = max(window, self.config.min_window_steps)

        trigger_by_chars = _estimate_prompt_chars(state, window_steps=window) > self.config.max_prompt_chars

        if trigger_by_steps or trigger_by_chars:
            self._update_abstract(state)
            state["steps_since_compress"] = 0

        # Always enforce window to fit max_prompt_chars (shrink if needed).
        window = int(state.get("prompt_window_steps", self.config.default_window_steps))
        window = max(window, self.config.min_window_steps)
        while window > self.config.min_window_steps:
            if _estimate_prompt_chars(state, window_steps=window) <= self.config.max_prompt_chars:
                break
            window -= 1
        state["prompt_window_steps"] = window
        return state

    def _update_abstract(self, state: dict[str, Any]) -> None:
        task = state.get("task")
        question = getattr(task, "question", "") if task is not None else ""

        steps = state.get("steps") or []
        if not isinstance(steps, list):
            return

        # Only summarize older steps (exclude the most recent window to keep local coherence).
        window = int(state.get("prompt_window_steps", self.config.default_window_steps))
        older = steps[: -window] if len(steps) > window else []
        if not older:
            return

        # Prune older steps aggressively before summarization.
        pruned_older, _ = prune_for_prompt(
            older,
            max_string_chars=self.config.observation_max_string_chars,
            max_list_items=self.config.observation_max_list_items,
        )

        abstract = state.get("abstract") or {}
        abstract_json = _safe_json(abstract)

        prompt = (
            "You are maintaining a structured working memory (abstract) for a data agent.\n"
            "Update the abstract using the older steps below. Keep it compact and factual.\n\n"
            "Return ONLY one JSON object with exactly these keys:\n"
            "- assets: { files: [string], tables: [string] }\n"
            "- facts: [ { k: string, v: (string|number|boolean|null), source: string } ]\n"
            "- open_questions: [string]\n\n"
            f"Limits:\n"
            f"- files <= {self.config.max_files}\n"
            f"- tables <= {self.config.max_tables}\n"
            f"- facts <= {self.config.max_facts}\n"
            f"- open_questions <= {self.config.max_open_questions}\n\n"
            f"Question:\n{question}\n\n"
            f"Current abstract JSON:\n{abstract_json}\n\n"
            f"Older steps (pruned JSON):\n{_safe_json(pruned_older)}\n"
        )

        raw = self.model.complete([ModelMessage(role="system", content="You output JSON only."), ModelMessage(role="user", content=prompt)])
        # Best-effort parse; if parsing fails, keep old abstract (avoid breaking runs).
        try:
            new_abs = json.loads(_strip_fence(raw)) if isinstance(raw, str) else {}
            if isinstance(new_abs, dict):
                # Normalize + enforce limits.
                assets = new_abs.get("assets") if isinstance(new_abs.get("assets"), dict) else {}
                files = assets.get("files") if isinstance(assets.get("files"), list) else []
                tables = assets.get("tables") if isinstance(assets.get("tables"), list) else []
                facts = new_abs.get("facts") if isinstance(new_abs.get("facts"), list) else []
                open_q = new_abs.get("open_questions") if isinstance(new_abs.get("open_questions"), list) else []

                files = [str(x) for x in files][-self.config.max_files :]
                tables = [str(x) for x in tables][-self.config.max_tables :]
                open_q = [str(x) for x in open_q][-self.config.max_open_questions :]
                # facts: keep last N valid items
                norm_facts = []
                for item in facts:
                    if not isinstance(item, dict):
                        continue
                    k = str(item.get("k", "")).strip()
                    if not k:
                        continue
                    norm_facts.append(
                        {
                            "k": k,
                            "v": item.get("v"),
                            "source": str(item.get("source", "")).strip(),
                        }
                    )
                norm_facts = norm_facts[-self.config.max_facts :]

                state["abstract"] = {
                    "assets": {"files": files, "tables": tables},
                    "facts": norm_facts,
                    "open_questions": open_q,
                }
        except Exception:  # noqa: BLE001
            return
