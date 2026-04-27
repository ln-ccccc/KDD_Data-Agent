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
