from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import os
import yaml

from data_agent_baseline.core.types import RetryPolicy, TraceLevel

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_dataset_root() -> Path:
    return PROJECT_ROOT / "data" / "public" / "input"


def _default_run_output_dir() -> Path:
    return PROJECT_ROOT / "artifacts" / "runs"


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    root_path: Path = field(default_factory=_default_dataset_root)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    model: str = "gemini-2.5-flash"
    api_base: str = ""
    api_key: str = ""
    max_steps: int = 64
    temperature: float = 0.0
    # react: original hand-rolled ReAct loop
    # hybrid: planner -> ReAct (hand-rolled)
    # graph: LangGraph-based ReAct loop (same JSON action protocol)
    # hybrid_graph: planner -> LangGraph ReAct
    mode: str = "react"
    seed: int | None = 42
    seed_enabled: bool = True
    request_timeout_seconds: float = 60.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    # Context / memory knobs (lightweight, for benchmark stability)
    max_prompt_chars: int = 24_000
    compress_trigger_steps: int = 12
    history_max_steps: int = 16


@dataclass(frozen=True, slots=True)
class RunConfig:
    output_dir: Path = field(default_factory=_default_run_output_dir)
    run_id: str | None = None
    max_workers: int = 4
    task_timeout_seconds: int = 600
    trace_level: TraceLevel = "standard"


@dataclass(frozen=True, slots=True)
class ModelsConfig:
    embedding_path: str = "Alibaba-NLP/gte-Qwen2-1.5B-instruct"
    schema_embedding_path: str = "Alibaba-NLP/gte-Qwen2-1.5B-instruct"


@dataclass(frozen=True, slots=True)
class AppConfig:
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    run: RunConfig = field(default_factory=RunConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)


def _path_value(raw_value: str | None, default_value: Path) -> Path:
    if not raw_value:
        return default_value
    candidate = Path(raw_value)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def load_app_config(config_path: Path) -> AppConfig:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    dataset_defaults = DatasetConfig()
    agent_defaults = AgentConfig()
    run_defaults = RunConfig()

    dataset_payload = payload.get("dataset", {})
    agent_payload = payload.get("agent", {})
    run_payload = payload.get("run", {})
    models_payload = payload.get("models", {})

    retry_payload = agent_payload.get("retry", {}) or {}
    retry_defaults = agent_defaults.retry.normalized()
    retry_policy = RetryPolicy(
        max_retries=int(retry_payload.get("max_retries", retry_defaults.max_retries)),
        backoff_seconds=float(retry_payload.get("backoff_seconds", retry_defaults.backoff_seconds)),
    ).normalized()

    dataset_config = DatasetConfig(
        root_path=_path_value(dataset_payload.get("root_path"), dataset_defaults.root_path),
    )
    api_base_val = (
        os.environ.get("DATA_AGENT_API_BASE")
        or os.environ.get("MODEL_API_URL")
        or agent_payload.get("api_base", agent_defaults.api_base)
    )
    api_key_val = (
        os.environ.get("DATA_AGENT_API_KEY")
        or os.environ.get("MODEL_API_KEY")
        or agent_payload.get("api_key", agent_defaults.api_key)
    )
    model_val = os.environ.get("MODEL_NAME") or agent_payload.get("model", agent_defaults.model)

    agent_config = AgentConfig(
        model=str(model_val),
        api_base=str(api_base_val),
        api_key=str(api_key_val),
        max_steps=int(agent_payload.get("max_steps", agent_defaults.max_steps)),
        temperature=float(agent_payload.get("temperature", agent_defaults.temperature)),
        mode=str(agent_payload.get("mode", agent_defaults.mode)),
        seed=agent_payload.get("seed", agent_defaults.seed),
        seed_enabled=bool(agent_payload.get("seed_enabled", agent_defaults.seed_enabled)),
        request_timeout_seconds=float(
            agent_payload.get("request_timeout_seconds", agent_defaults.request_timeout_seconds)
        ),
        retry=retry_policy,
        max_prompt_chars=int(agent_payload.get("max_prompt_chars", agent_defaults.max_prompt_chars)),
        compress_trigger_steps=int(
            agent_payload.get("compress_trigger_steps", agent_defaults.compress_trigger_steps)
        ),
        history_max_steps=int(agent_payload.get("history_max_steps", agent_defaults.history_max_steps)),
    )
    raw_run_id = run_payload.get("run_id")
    run_id = run_defaults.run_id
    if raw_run_id is not None:
        normalized_run_id = str(raw_run_id).strip()
        run_id = normalized_run_id or None

    trace_level_raw = str(run_payload.get("trace_level", run_defaults.trace_level)).strip().lower()
    if trace_level_raw not in {"minimal", "standard", "verbose"}:
        trace_level_raw = run_defaults.trace_level

    run_config = RunConfig(
        output_dir=_path_value(run_payload.get("output_dir"), run_defaults.output_dir),
        run_id=run_id,
        max_workers=int(run_payload.get("max_workers", run_defaults.max_workers)),
        task_timeout_seconds=int(run_payload.get("task_timeout_seconds", run_defaults.task_timeout_seconds)),
        trace_level=trace_level_raw,
    )

    models_defaults = ModelsConfig()
    models_config = ModelsConfig(
        embedding_path=os.environ.get("EMBEDDING_MODEL_PATH")
        or models_payload.get("embedding_path", models_defaults.embedding_path),
        schema_embedding_path=os.environ.get("SCHEMA_EMBEDDING_MODEL_PATH")
        or models_payload.get("schema_embedding_path", models_defaults.schema_embedding_path),
    )

    return AppConfig(
        dataset=dataset_config, agent=agent_config, run=run_config, models=models_config
    )
