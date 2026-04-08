"""
运行时记录器，用于记录任务运行时的中间结果。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


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

