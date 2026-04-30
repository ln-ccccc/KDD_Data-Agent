# runner for data agent baseline
"""
定义数据代理基准线的运行器，包括任务运行、结果写入、失败处理等。
"""
from __future__ import annotations

import csv
import multiprocessing
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from data_agent_baseline.agents.model import OpenAIModelAdapter
from data_agent_baseline.orchestrators.langgraph.plan_execute_graph import (
    LangGraphPlanAndExecuteAgent,
    LangGraphPlanAndExecuteConfig,
)
from data_agent_baseline.orchestrators.langgraph.react_graph import LangGraphReActAgent, LangGraphReActAgentConfig
from data_agent_baseline.agents.react import ReActAgent, ReActAgentConfig
from data_agent_baseline.agents.plan_execute import PlanAndExecuteAgent
from data_agent_baseline.benchmark.dataset import DABenchPublicDataset
from data_agent_baseline.config import AppConfig
from data_agent_baseline.runtime.trace import prune_trace_payload, write_json
from data_agent_baseline.tools.registry import ToolRegistry, create_default_tool_registry
from data_agent_baseline.tools.schema_catalog import save_schema_catalog


@dataclass(frozen=True, slots=True)
class TaskRunArtifacts:
    """
    记录任务运行后的工件，包括任务ID、输出目录、预测CSV路径、跟踪路径、是否成功、失败原因等。
    """
    task_id: str
    task_output_dir: Path
    prediction_csv_path: Path | None
    trace_path: Path
    succeeded: bool
    failure_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_output_dir": str(self.task_output_dir),
            "prediction_csv_path": str(self.prediction_csv_path) if self.prediction_csv_path else None,
            "trace_path": str(self.trace_path),
            "succeeded": self.succeeded,
            "failure_reason": self.failure_reason,
        }


def create_run_id() -> str:
    """
    创建一个唯一的运行ID，格式为YYYYMMDDTHHMMSS。
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_run_id(run_id: str | None = None) -> str:
    """
    解析运行ID，确保它是一个有效的目录名。
    """
    if run_id is None:
        return create_run_id()

    normalized = run_id.strip()
    if not normalized:
        raise ValueError("run_id must not be empty.")
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise ValueError("run_id must be a single directory name, not a path.")
    return normalized


def create_run_output_dir(output_root: Path, *, run_id: str | None = None) -> tuple[str, Path]:
    """
    创建运行输出目录，确保它不存在。
    返回运行ID和运行输出目录路径。
    """
    if False:
        raise ValueError(f"output_root {output_root} must not exist.")
    effective_run_id = resolve_run_id(run_id)
    run_output_dir = output_root / effective_run_id
    run_output_dir.mkdir(parents=True, exist_ok=False)
    return effective_run_id, run_output_dir


def build_model_adapter(config: AppConfig) -> OpenAIModelAdapter:
    """
    创建OpenAI模型适配器，根据配置文件设置。
    """
    return OpenAIModelAdapter(
        model=config.agent.model,
        api_base=config.agent.api_base,
        api_key=config.agent.api_key,
        temperature=config.agent.temperature,
        seed=config.agent.seed,
        seed_enabled=getattr(config.agent, "seed_enabled", False),
        request_timeout_seconds=getattr(config.agent, "request_timeout_seconds", 60.0),
        retry=config.agent.retry,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    
    # Backward-compatible wrapper (older code paths may still call _write_json).
    write_json(path, payload)


def _write_csv(path: Path, columns: list[str], rows: list[list[Any]]) -> None:
    """
    写入CSV文件，确保目录存在。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)


def _failure_run_result_payload(task_id: str, failure_reason: str) -> dict[str, Any]:
    """
    构建任务运行失败的JSON payload，包含任务ID、失败原因、是否成功等。
    """
    return {
        "task_id": task_id,
        "answer": None,
        "steps": [],
        "failure_reason": failure_reason,
        "succeeded": False,
    }


def _run_single_task_core(
    *,
    task_id: str,
    config: AppConfig,
    model=None,
    tools: ToolRegistry | None = None,
) -> dict[str, Any]:
    """
    运行单个任务，返回任务结果。
    """
    public_dataset = DABenchPublicDataset(config.dataset.root_path)
    task = public_dataset.get_task(task_id)

    selected_model = model or build_model_adapter(config)
    selected_tools = tools or create_default_tool_registry(
        allow_python=getattr(config.agent, "allow_python_exec", True),
        model=selected_model,
        config=config,
    )
    mode = (getattr(config.agent, "mode", "react") or "react").lower()
    if mode in {"hybrid", "plan_execute"}:
        agent = PlanAndExecuteAgent(
            model=selected_model,
            tools=selected_tools,
            config=ReActAgentConfig(max_steps=config.agent.max_steps),
        )
    elif mode in {"graph", "langgraph"}:
        agent = LangGraphReActAgent(
            model=selected_model,
            tools=selected_tools,
            config=LangGraphReActAgentConfig(
                max_steps=config.agent.max_steps,
                history_max_steps=getattr(config.agent, "history_max_steps", 8),
                max_prompt_chars=getattr(config.agent, "max_prompt_chars", 24_000),
                compress_trigger_steps=getattr(config.agent, "compress_trigger_steps", 12),
            ),
        )
    elif mode in {"hybrid_graph", "graph_hybrid"}:
        agent = LangGraphPlanAndExecuteAgent(
            model=selected_model,
            tools=selected_tools,
            config=LangGraphPlanAndExecuteConfig(
                max_steps=config.agent.max_steps,
                history_max_steps=getattr(config.agent, "history_max_steps", 8),
                max_prompt_chars=getattr(config.agent, "max_prompt_chars", 24_000),
                compress_trigger_steps=getattr(config.agent, "compress_trigger_steps", 12),
            ),
        )
    else:
        agent = ReActAgent(
            model=selected_model,
            tools=selected_tools,
            config=ReActAgentConfig(max_steps=config.agent.max_steps),
        )
    run_result = agent.run(task)
    return run_result.to_dict()


def _run_single_task_in_subprocess(task_id: str, config: AppConfig, queue: multiprocessing.Queue[Any]) -> None:
    """
    在子进程中运行运行单个任务，将结果通过队列返回。
    """
    import sys
    try:
        res = _run_single_task_core(task_id=task_id, config=config)
        queue.put({"ok": True, "run_result": res})
    except BaseException as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        queue.put({"ok": False, "error": str(exc)})


def _run_single_task_with_timeout(*, task_id: str, config: AppConfig) -> dict[str, Any]:
    """
    运行单个任务，设置超时时间。
    """
    public_dataset = DABenchPublicDataset(config.dataset.root_path)
    task = public_dataset.get_task(task_id)
    
    # Dynamic timeout based on difficulty
    base_timeout = config.run.task_timeout_seconds
    if base_timeout > 0:
        if task.difficulty == "hard":
            timeout_seconds = base_timeout * 1.5
        elif task.difficulty == "extreme":
            timeout_seconds = base_timeout * 2.0
        else:
            timeout_seconds = base_timeout
    else:
        timeout_seconds = base_timeout

    if timeout_seconds <= 0:
        return _run_single_task_core(task_id=task_id, config=config)

    import queue as q
    queue_obj: multiprocessing.Queue[Any] = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_run_single_task_in_subprocess,
        args=(task_id, config, queue_obj),
    )
    process.start()

    try:
        # get blocks until an item is available or timeout expires
        result = queue_obj.get(timeout=timeout_seconds)
    except q.Empty:
        result = None

    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join()
        
        if result is None:
            return {
                "task_id": task_id,
                "succeeded": False,
                "failure_reason": f"Task timed out after {timeout_seconds} seconds. Process killed.",
                "steps": [{"thought": f"Process stuck or timed out after {timeout_seconds}s.", "action": "timeout", "action_input": {}, "observation": {"ok": False, "content": "Killed by runner"}}],
                "answer": None
            }

    if result is not None:
        if result["ok"]:
            return result["run_result"]
        else:
            return {
                "task_id": task_id,
                "succeeded": False,
                "failure_reason": f"Task failed with error: {result['error']}",
                "steps": [{"thought": f"Task failed with error: {result['error']}", "action": "error", "action_input": {}, "observation": {"ok": False, "content": "Error"}}],
                "answer": None
            }

    exit_code = process.exitcode
    if exit_code not in (None, 0):
        return _failure_run_result_payload(
            task_id,
            f"Task exited unexpectedly with exit code {exit_code}.",
        )
    
    return _failure_run_result_payload(task_id, "Task exited without returning a result.")


def _write_task_outputs(task_id: str, run_output_dir: Path, run_result: dict[str, Any], task: Any = None) -> TaskRunArtifacts:
    """
    写入任务运行输出，包括trace.json和prediction.csv。
    """
    task_output_dir = run_output_dir / task_id
    task_output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = task_output_dir / "trace.json"
    trace_level = str(run_result.get("trace_level", "standard") or "standard").strip().lower()
    trace_payload = prune_trace_payload(dict(run_result), trace_level=trace_level)
    _write_json(trace_path, trace_payload)

    # 1. 提取 SchemaCatalog 并存为 JSON，用作后续分析或二次索引
    if task:
        try:
            save_schema_catalog(task, task_output_dir)
        except Exception as e:
            import traceback
            traceback.print_exc()

    prediction_csv_path: Path | None = None
    answer = run_result.get("answer")
    if isinstance(answer, dict):
        prediction_csv_path = task_output_dir / "prediction.csv"
        final_sql = answer.get("final_sql")
        file_path = answer.get("file_path")
        if isinstance(final_sql, str) and final_sql.strip():
            try:
                from data_agent_baseline.tools.duckdb_runtime import export_query_to_csv

                export_query_to_csv(
                    context_dir=task.context_dir if task is not None else run_output_dir,
                    sql=final_sql,
                    output_csv_path=prediction_csv_path,
                )
            except Exception:
                import traceback

                traceback.print_exc()
        elif isinstance(file_path, str) and file_path.strip():
            import os
            import shutil

            src_path = file_path
            if task is not None and not os.path.isabs(file_path):
                src_path = str(task.context_dir / file_path)
            try:
                shutil.copy(src_path, prediction_csv_path)
            except Exception:
                import traceback

                traceback.print_exc()
        else:
            _write_csv(
                prediction_csv_path,
                list(answer.get("columns", [])),
                [list(row) for row in answer.get("rows", [])],
            )

    return TaskRunArtifacts(
        task_id=task_id,
        task_output_dir=task_output_dir,
        prediction_csv_path=prediction_csv_path,
        trace_path=trace_path,
        succeeded=bool(run_result.get("succeeded")),
        failure_reason=run_result.get("failure_reason"),
    )


def run_single_task(
    *,
    task_id: str,
    config: AppConfig,
    run_output_dir: Path,
    model=None,
    tools: ToolRegistry | None = None,
) -> TaskRunArtifacts:
    """
    运行单个任务，返回任务结果。
    """
    started_at = perf_counter()
    if model is None and tools is None:
        run_result = _run_single_task_with_timeout(task_id=task_id, config=config)
    else:
        run_result = _run_single_task_core(task_id=task_id, config=config, model=model, tools=tools)
    run_result["e2e_elapsed_seconds"] = round(perf_counter() - started_at, 3)
    run_result["trace_level"] = getattr(config.run, "trace_level", "standard")
    
    # fetch task
    public_dataset = DABenchPublicDataset(config.dataset.root_path)
    task = public_dataset.get_task(task_id)
    
    return _write_task_outputs(task_id, run_output_dir, run_result, task=task)


def run_benchmark(
    *,
    config: AppConfig,
    model=None,
    tools: ToolRegistry | None = None,
    limit: int | None = None,
    progress_callback: Callable[[TaskRunArtifacts], None] | None = None,
) -> tuple[Path, list[TaskRunArtifacts]]:
    """
    运行基准测试，返回任务结果。
    """
    effective_run_id, run_output_dir = create_run_output_dir(config.run.output_dir, run_id=config.run.run_id)

    dataset = DABenchPublicDataset(config.dataset.root_path)
    tasks = dataset.iter_tasks()
    if limit is not None:
        tasks = tasks[:limit]

    effective_workers = config.run.max_workers
    if effective_workers < 1:
        raise ValueError("max_workers must be at least 1.")
    if model is not None or tools is not None:
        effective_workers = 1

    task_ids = [task.task_id for task in tasks]

    task_artifacts: list[TaskRunArtifacts]
    if effective_workers == 1:
        shared_model = model or build_model_adapter(config)
        shared_tools = tools or create_default_tool_registry(
            allow_python=getattr(config.agent, "allow_python_exec", True),
            model=shared_model,
            config=config,
        )
        task_artifacts = []
        for task_id in task_ids:
            artifact = run_single_task(
                task_id=task_id,
                config=config,
                run_output_dir=run_output_dir,
                model=shared_model,
                tools=shared_tools,
            )
            task_artifacts.append(artifact)
            if progress_callback is not None:
                progress_callback(artifact)
    else:
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            future_to_index = {
                executor.submit(
                    run_single_task,
                    task_id=task_id,
                    config=config,
                    run_output_dir=run_output_dir,
                ): index
                for index, task_id in enumerate(task_ids)
            }
            indexed_artifacts: list[TaskRunArtifacts | None] = [None] * len(task_ids)
            for future in as_completed(future_to_index):
                artifact = future.result()
                indexed_artifacts[future_to_index[future]] = artifact
                if progress_callback is not None:
                    progress_callback(artifact)
            task_artifacts = [artifact for artifact in indexed_artifacts if artifact is not None]

    summary_path = run_output_dir / "summary.json"
    _write_json(
        summary_path,
        {
            "run_id": effective_run_id,
            "task_count": len(task_artifacts),
            "succeeded_task_count": sum(1 for artifact in task_artifacts if artifact.succeeded),
            "max_workers": effective_workers,
            "tasks": [artifact.to_dict() for artifact in task_artifacts],
        },
    )
    return run_output_dir, task_artifacts
