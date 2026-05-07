from __future__ import annotations

import json
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=8)
def _get_tiktoken_encoding(model_name: str):
    try:
        import tiktoken

        return tiktoken.encoding_for_model(model_name)
    except Exception:
        return None


def count_text_tokens(text: str, *, model_name: str) -> int:
    if not text:
        return 0
    enc = _get_tiktoken_encoding(model_name)
    if enc is None:
        # Cheap fallback: ~4 chars/token for mixed EN/CN prompts.
        return max(1, len(text) // 4)
    return len(enc.encode(text))


def count_json_tokens(payload: Any, *, model_name: str) -> int:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        text = str(payload)
    return count_text_tokens(text, model_name=model_name)

