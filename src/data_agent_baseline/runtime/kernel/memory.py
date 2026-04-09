from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.runtime.context_pruning import prune_for_prompt
from data_agent_baseline.runtime.kernel.state import AbstractMemory, RuntimeState


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


@dataclass(frozen=True, slots=True)
class AbstractUpdateConfig:
    max_files: int = 50
    max_tables: int = 50
    max_facts: int = 30
    max_open_questions: int = 10

    observation_max_string_chars: int = 4000
    observation_max_list_items: int = 50


def estimate_prompt_chars(*, question: str, abstract: dict[str, Any], steps: list[dict[str, Any]]) -> int:
    """Cheap character estimate used by policy; deterministic."""
    size = len(question) + len(json.dumps(abstract, ensure_ascii=False, default=str))
    for step in steps:
        size += len(str(step.get("raw_response", "")))
        size += len(json.dumps(step.get("observation", {}), ensure_ascii=False, default=str))
    return size


class AbstractUpdater:
    """LLM-based abstract updater (mechanism)."""

    def __init__(self, *, model: ModelAdapter, config: AbstractUpdateConfig | None = None) -> None:
        self.model = model
        self.config = config or AbstractUpdateConfig()

    def update(self, *, question: str, current: AbstractMemory, older_steps: list[dict[str, Any]]) -> AbstractMemory | None:
        if not older_steps:
            return None

        pruned_older, _ = prune_for_prompt(
            older_steps,
            max_string_chars=self.config.observation_max_string_chars,
            max_list_items=self.config.observation_max_list_items,
        )

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
            f"Current abstract JSON:\n{current.model_dump_json(ensure_ascii=False)}\n\n"
            f"Older steps (pruned JSON):\n{json.dumps(pruned_older, ensure_ascii=False, default=str)}\n"
        )

        raw = self.model.complete(
            [
                ModelMessage(role="system", content="You output JSON only."),
                ModelMessage(role="user", content=prompt),
            ]
        )
        try:
            payload = json.loads(_strip_fence(raw))
        except Exception:  # noqa: BLE001
            return None

        if not isinstance(payload, dict):
            return None

        # Best-effort normalization + limit enforcement
        assets = payload.get("assets") if isinstance(payload.get("assets"), dict) else {}
        files = assets.get("files") if isinstance(assets.get("files"), list) else []
        tables = assets.get("tables") if isinstance(assets.get("tables"), list) else []
        facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
        open_q = payload.get("open_questions") if isinstance(payload.get("open_questions"), list) else []

        files = [str(x) for x in files][-self.config.max_files :]
        tables = [str(x) for x in tables][-self.config.max_tables :]
        open_q = [str(x) for x in open_q][-self.config.max_open_questions :]

        norm_facts: list[dict[str, Any]] = []
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

        return AbstractMemory(assets={"files": files, "tables": tables}, facts=norm_facts, open_questions=open_q)


def schedule_auto_recall_after_sql_error(state: RuntimeState, *, path: str | None) -> None:
    # Only schedule; policy decides whether to inject.
    if not path:
        return
    state.pending_auto_recall = {
        "kind": "sqlite_schema",
        "query": {"path_contains": str(path).split("/")[-1]},
        "top_k": 1,
    }

