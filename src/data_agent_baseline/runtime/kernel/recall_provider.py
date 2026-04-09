from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from data_agent_baseline.runtime.context_pruning import prune_for_prompt
from data_agent_baseline.runtime.kernel.state import RuntimeState


RecallKind = Literal["sqlite_schema", "context_listing"]


@dataclass(frozen=True, slots=True)
class RecallProviderConfig:
    max_top_k: int = 3
    max_string_chars: int = 2000
    max_list_items: int = 30


class RecallProvider:
    """Internal memory read (NOT a tool)."""

    def __init__(self, config: RecallProviderConfig | None = None) -> None:
        self.config = config or RecallProviderConfig()

    def recall(self, *, state: RuntimeState, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("kind")
        query = request.get("query") or {}
        top_k = int(request.get("top_k", 1))
        top_k = max(1, min(top_k, self.config.max_top_k))

        if kind not in ("sqlite_schema", "context_listing"):
            return {"ok": False, "error": {"type": "InvalidRecallKind", "message": f"Unsupported kind: {kind}"}}
        if not isinstance(query, dict):
            return {"ok": False, "error": {"type": "InvalidRecallQuery", "message": "query must be an object"}}

        matches: list[dict[str, Any]] = []

        if kind == "sqlite_schema":
            allowed_keys = {"path_contains"}
            extra = set(query.keys()) - allowed_keys
            if extra:
                return {
                    "ok": False,
                    "error": {
                        "type": "InvalidRecallQueryKeys",
                        "message": f"Unsupported query keys for sqlite_schema: {sorted(extra)}",
                        "allowed_keys": sorted(allowed_keys),
                    },
                }
            path_contains = str(query.get("path_contains", "") or "").strip()
            for step in reversed(state.steps):
                if step.action != "inspect_sqlite_schema":
                    continue
                obs = step.observation or {}
                content = obs.get("content") if isinstance(obs, dict) else None
                if not isinstance(content, dict):
                    continue
                if path_contains and path_contains not in str(content.get("path", "")):
                    continue
                snippet, truncated = prune_for_prompt(
                    content,
                    max_string_chars=self.config.max_string_chars,
                    max_list_items=self.config.max_list_items,
                )
                matches.append(
                    {
                        "step_index": step.step_index,
                        "tool": "inspect_sqlite_schema",
                        "snippet": snippet,
                        "truncated": truncated,
                    }
                )
                if len(matches) >= top_k:
                    break

        if kind == "context_listing":
            allowed_keys = {"contains"}
            extra = set(query.keys()) - allowed_keys
            if extra:
                return {
                    "ok": False,
                    "error": {
                        "type": "InvalidRecallQueryKeys",
                        "message": f"Unsupported query keys for context_listing: {sorted(extra)}",
                        "allowed_keys": sorted(allowed_keys),
                    },
                }
            contains = query.get("contains") or []
            if not isinstance(contains, list):
                return {
                    "ok": False,
                    "error": {"type": "InvalidRecallQuery", "message": "query.contains must be a list of strings"},
                }
            contains = [str(x) for x in contains if str(x)]
            for step in reversed(state.steps):
                if step.action != "list_context":
                    continue
                obs = step.observation or {}
                content = obs.get("content") if isinstance(obs, dict) else None
                if not isinstance(content, dict):
                    continue
                entries = content.get("entries")
                if not isinstance(entries, list):
                    continue
                if contains:
                    filtered = [e for e in entries if any(c in str(e.get("path", "")) for c in contains)]
                    content = {"root": content.get("root"), "entries": filtered}
                snippet, truncated = prune_for_prompt(
                    content,
                    max_string_chars=self.config.max_string_chars,
                    max_list_items=self.config.max_list_items,
                )
                matches.append(
                    {
                        "step_index": step.step_index,
                        "tool": "list_context",
                        "snippet": snippet,
                        "truncated": truncated,
                    }
                )
                if len(matches) >= top_k:
                    break

        return {
            "ok": True,
            "kind": kind,
            "top_k": top_k,
            "matches": matches,
            "match_count": len(matches),
            "truncated": any(m.get("truncated") for m in matches),
        }

