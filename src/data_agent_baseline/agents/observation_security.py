from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


SUSPICIOUS_PREFIXES = (
    "ignore previous",
    "system:",
    "assistant:",
    "developer:",
    "execute:",
    "run:",
)


@dataclass(frozen=True, slots=True)
class ExposureConfig:
    max_chars: int = 1200
    max_lines: int = 24
    max_items: int = 12


@dataclass(frozen=True, slots=True)
class ObservationExposure:
    trust: str
    taint_level: str
    summary: dict[str, Any]
    exposed: dict[str, Any]


def _compact_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_instruction_like(text: str) -> bool:
    normalized = text.strip().lower()
    return any(normalized.startswith(prefix) for prefix in SUSPICIOUS_PREFIXES)


def _truncate_text(text: str, cfg: ExposureConfig) -> tuple[str, bool]:
    lines = text.splitlines()
    cropped_lines = lines[: cfg.max_lines]
    retained_lines: list[str] = []
    dropped_instruction_like = 0
    for line in cropped_lines:
        if _is_instruction_like(line):
            dropped_instruction_like += 1
            continue
        retained_lines.append(line)
    joined = "\n".join(retained_lines)
    truncated = len(lines) > cfg.max_lines or len(joined) > cfg.max_chars
    return joined[: cfg.max_chars], truncated or dropped_instruction_like > 0


def _sanitize_value(value: Any, cfg: ExposureConfig, depth: int = 0) -> Any:
    if depth > 3:
        return "<depth_limited>"
    if isinstance(value, str):
        clipped, truncated = _truncate_text(value, cfg)
        if truncated:
            return {"type": "text", "preview": clipped, "truncated": True}
        return {"type": "text", "preview": clipped, "truncated": False}
    if isinstance(value, list):
        trimmed = value[: cfg.max_items]
        sanitized_items = [_sanitize_value(item, cfg, depth + 1) for item in trimmed]
        return {
            "type": "list",
            "size": len(value),
            "preview": sanitized_items,
            "truncated": len(value) > cfg.max_items,
        }
    if isinstance(value, dict):
        keys = sorted(value.keys())[: cfg.max_items]
        mapped = {str(key): _sanitize_value(value[key], cfg, depth + 1) for key in keys}
        return {
            "type": "object",
            "keys": [str(key) for key in keys],
            "preview": mapped,
            "truncated": len(value) > cfg.max_items,
        }
    return value


def _taint_level_from_observation(observation: dict[str, Any]) -> str:
    if not observation.get("ok", False):
        return "high"
    tool_name = str(observation.get("tool", ""))
    if tool_name in {"read_doc", "read_json", "read_csv", "execute_python"}:
        return "high"
    if tool_name in {"execute_context_sql"}:
        return "low"
    return "none"


def build_observation_exposure(
    observation: dict[str, Any],
    *,
    config: ExposureConfig | None = None,
) -> ObservationExposure:
    cfg = config or ExposureConfig()
    taint_level = _taint_level_from_observation(observation)
    trust = "untrusted" if taint_level in {"high", "low"} else "trusted"
    content = observation.get("content")
    exposed_content = _sanitize_value(content, cfg)
    summary = {
        "tool": observation.get("tool"),
        "ok": observation.get("ok"),
        "taint_level": taint_level,
        "content_hash": _compact_hash(content),
    }
    exposed = {
        "risk_label": "UNTRUSTED_DATA" if trust == "untrusted" else "TRUSTED_DATA",
        "content": exposed_content,
    }
    return ObservationExposure(
        trust=trust,
        taint_level=taint_level,
        summary=summary,
        exposed=exposed,
    )
