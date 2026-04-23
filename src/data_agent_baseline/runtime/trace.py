"""
运行时记录器，用于记录任务运行时的中间结果。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from data_agent_baseline.core.types import TraceLevel


def _default_json_serializer(obj: Any):
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def write_json(path: Path, payload: Any) -> None:
    """
    写入JSON数据到指定路径，确保目录存在。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_default_json_serializer) + "\n",
        encoding="utf-8",
    )


def _truncate_text(value: str, *, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        return value, False
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True


def _truncate_obj(obj: Any, *, max_chars: int) -> tuple[Any, bool]:
    """Recursively truncate long strings inside dict/list payloads.

    Returns (new_obj, truncated_flag).
    """
    truncated = False
    if isinstance(obj, str):
        new, did = _truncate_text(obj, max_chars=max_chars)
        return new, did
    if isinstance(obj, list):
        new_list = []
        for item in obj:
            new_item, did = _truncate_obj(item, max_chars=max_chars)
            truncated = truncated or did
            new_list.append(new_item)
        return new_list, truncated
    if isinstance(obj, dict):
        new_dict: dict[Any, Any] = {}
        for key, value in obj.items():
            new_value, did = _truncate_obj(value, max_chars=max_chars)
            truncated = truncated or did
            new_dict[key] = new_value
        return new_dict, truncated
    return obj, False


def prune_trace_payload(
    payload: dict[str, Any],
    *,
    trace_level: TraceLevel = "standard",
    max_string_chars: int = 8000,
) -> dict[str, Any]:
    """Prune and truncate trace.json payload to keep it usable.

    - minimal: remove large fields (raw_response, thought) and keep essential observations.
    - standard: keep raw_response but truncate long strings.
    - verbose: currently same as standard (reserved for future extensions).
    """
    normalized_level = (trace_level or "standard").strip().lower()
    if normalized_level not in {"minimal", "standard", "verbose"}:
        normalized_level = "standard"

    pruned = dict(payload)
    steps = pruned.get("steps")
    if isinstance(steps, list):
        new_steps: list[dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_copy = dict(step)
            if normalized_level == "minimal":
                # Keep only essentials for debugging while avoiding huge trace files.
                step_copy.pop("raw_response", None)
                step_copy.pop("thought", None)
                # action_input can be large; keep it but truncate strings within.
            # Truncate long strings in step fields (including observation/content).
            step_copy, did_truncate = _truncate_obj(step_copy, max_chars=max_string_chars)
            if did_truncate:
                obs = step_copy.get("observation")
                if isinstance(obs, dict):
                    meta = obs.get("meta")
                    if not isinstance(meta, dict):
                        meta = {}
                        obs["meta"] = meta
                    meta["truncated"] = True
            new_steps.append(step_copy)
        pruned["steps"] = new_steps

    # Also truncate top-level failure_reason etc.
    pruned, _ = _truncate_obj(pruned, max_chars=max_string_chars)
    pruned["trace_level"] = normalized_level
    return pruned
