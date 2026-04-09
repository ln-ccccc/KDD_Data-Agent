from __future__ import annotations

from typing import Any


def _truncate_text(value: str, *, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        return value, False
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True


def prune_for_prompt(obj: Any, *, max_string_chars: int = 4000, max_list_items: int = 50) -> tuple[Any, bool]:
    """Prune arbitrary tool outputs before feeding them back to the LLM.

    Goal: reduce noise and prevent prompt/token explosion.
    This function is intentionally generic and conservative.
    """
    truncated = False
    if isinstance(obj, str):
        new, did = _truncate_text(obj, max_chars=max_string_chars)
        return new, did
    if isinstance(obj, list):
        new_list = []
        limited = obj[: max_list_items if max_list_items > 0 else len(obj)]
        if max_list_items > 0 and len(obj) > max_list_items:
            truncated = True
        for item in limited:
            new_item, did = prune_for_prompt(
                item, max_string_chars=max_string_chars, max_list_items=max_list_items
            )
            truncated = truncated or did
            new_list.append(new_item)
        return new_list, truncated
    if isinstance(obj, dict):
        new_dict: dict[Any, Any] = {}
        for key, value in obj.items():
            new_value, did = prune_for_prompt(
                value, max_string_chars=max_string_chars, max_list_items=max_list_items
            )
            truncated = truncated or did
            new_dict[key] = new_value
        return new_dict, truncated
    return obj, False

