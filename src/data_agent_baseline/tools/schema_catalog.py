from __future__ import annotations

from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.filesystem import get_data_schema


def build_schema_catalog(task: PublicTask) -> dict[str, Any]:
    catalog: dict[str, Any] = {"task_id": task.task_id, "files": []}
    for p in sorted(task.context_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(task.context_dir).as_posix()
        suffix = p.suffix.lower()
        if suffix in {".csv", ".json", ".parquet", ".xlsx", ".xls"}:
            try:
                schema = get_data_schema(task, rel)
            except Exception as e:
                schema = {"path": rel, "error": str(e)}
            catalog["files"].append(schema)
    return catalog


def save_schema_catalog(task: PublicTask, output_dir: Path) -> None:
    import json

    catalog = build_schema_catalog(task)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "schema_catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
