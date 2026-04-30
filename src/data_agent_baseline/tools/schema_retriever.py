from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _score_text(text: str, terms: list[str]) -> int:
    lowered = text.lower()
    return sum(1 for t in terms if t and t in lowered)


def retrieve_schema_context(catalog_path: Path, query: str, top_k: int = 10) -> dict[str, Any]:
    raw = json.loads(catalog_path.read_text(encoding="utf-8", errors="ignore"))
    q = (query or "").strip().lower()
    terms = [t for t in re.split(r"\s+", q) if t]
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in raw.get("files", []):
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", ""))
        schema = entry.get("schema", [])
        text_bits = [path]
        for row in schema:
            try:
                text_bits.append(" ".join(str(x) for x in row if x is not None))
            except Exception:
                continue
        score = _score_text("\n".join(text_bits), terms)
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return {"query": query, "top_k": top_k, "matches": [e for _, e in scored[: int(top_k)]]}
