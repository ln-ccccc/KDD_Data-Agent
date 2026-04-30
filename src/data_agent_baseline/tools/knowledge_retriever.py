from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask


def _iter_knowledge_files(task: PublicTask) -> list[Path]:
    candidates: list[Path] = []
    knowledge_md = task.context_dir / "knowledge.md"
    if knowledge_md.exists():
        candidates.append(knowledge_md)
    doc_dir = task.context_dir / "doc"
    if doc_dir.exists():
        for p in sorted(doc_dir.rglob("*")):
            if p.is_file() and p.suffix.lower() in {".md", ".txt"}:
                candidates.append(p)
    return candidates


def _extract_headings(text: str) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                headings.append(title)
    return headings


def list_knowledge_headings(task: PublicTask, query: str = "") -> dict[str, Any]:
    q = (query or "").strip().lower()
    headings: list[dict[str, Any]] = []
    for path in _iter_knowledge_files(task):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for h in _extract_headings(text):
            if not q or q in h.lower():
                headings.append({"path": path.relative_to(task.context_dir).as_posix(), "heading": h})
    return {"headings": headings}


def read_knowledge_section(task: PublicTask, heading: str) -> dict[str, Any]:
    target = (heading or "").strip()
    if not target:
        return {"error": "heading is required"}
    for path in _iter_knowledge_files(task):
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        start = None
        for i, line in enumerate(lines):
            if line.strip().lstrip("#").strip() == target and line.strip().startswith("#"):
                start = i
                break
        if start is None:
            continue
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].strip().startswith("#"):
                end = j
                break
        content = "\n".join(lines[start:end]).strip()
        return {"path": path.relative_to(task.context_dir).as_posix(), "heading": target, "content": content}
    return {"error": f"heading not found: {target}"}


def search_knowledge_doc(task: PublicTask, query: str) -> dict[str, Any]:
    q = (query or "").strip().lower()
    if not q:
        return {"error": "query is required"}
    terms = [t for t in re.split(r"\s+", q) if t]
    hits: list[dict[str, Any]] = []
    for path in _iter_knowledge_files(task):
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        score = sum(1 for t in terms if t in lowered)
        if score <= 0:
            continue
        preview = text[:1200].strip()
        hits.append(
            {
                "path": path.relative_to(task.context_dir).as_posix(),
                "score": score,
                "preview": preview,
            }
        )
    hits.sort(key=lambda x: x["score"], reverse=True)
    return {"query": query, "hits": hits[:5]}
