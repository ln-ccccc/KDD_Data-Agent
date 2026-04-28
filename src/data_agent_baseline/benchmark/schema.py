# schema for public task【静态结构】

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    difficulty: str
    question: str


@dataclass(frozen=True, slots=True)
class TaskAssets:
    task_dir: Path
    context_dir: Path


@dataclass(frozen=True, slots=True)
class PublicTask:
    record: TaskRecord
    assets: TaskAssets

    @property
    def task_id(self) -> str:
        return self.record.task_id

    @property
    def difficulty(self) -> str:
        return self.record.difficulty

    @property
    def question(self) -> str:
        return self.record.question

    @property
    def task_dir(self) -> Path:
        return self.assets.task_dir

    @property
    def context_dir(self) -> Path:
        return self.assets.context_dir


@dataclass(frozen=True, slots=True)
class AnswerTable:
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    final_sql: str | None = None
    file_path: str | None = None
    allow_empty_result: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {}
        if self.columns is not None:
            res["columns"] = list(self.columns)
        if self.rows is not None:
            res["rows"] = [list(row) for row in self.rows]
        if self.final_sql is not None:
            res["final_sql"] = self.final_sql
        if self.file_path is not None:
            res["file_path"] = self.file_path
        if self.allow_empty_result is not None:
            res["allow_empty_result"] = self.allow_empty_result
        return res
