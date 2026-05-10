from __future__ import annotations

import csv
import os
import signal
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import replace
from pathlib import Path

from data_agent_baseline.benchmark.dataset import DABenchPublicDataset
from data_agent_baseline.config import load_app_config
from data_agent_baseline.run.runner import run_single_task

GLOBAL_BUDGET_SECONDS = 11 * 3600


def _write_error_csv(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Error"])
        writer.writerow([message[:1000]])


def _read_eval_limit() -> int | None:
    raw_limit = os.environ.get("EVAL_TASK_LIMIT", "").strip()
    if not raw_limit:
        return None
    try:
        limit = int(raw_limit)
    except ValueError:
        print(f"Invalid EVAL_TASK_LIMIT={raw_limit!r}, ignored.", file=sys.stderr)
        return None
    if limit <= 0:
        print(f"Non-positive EVAL_TASK_LIMIT={raw_limit!r}, ignored.", file=sys.stderr)
        return None
    return limit


def _split_task_ids(dataset: DABenchPublicDataset, *, limit: int | None) -> tuple[list[str], list[str]]:
    tasks = dataset.iter_tasks()
    if limit is not None:
        tasks = tasks[:limit]
    easy_medium: list[str] = []
    hard_extreme: list[str] = []
    for task in tasks:
        if task.difficulty in {"easy", "medium"}:
            easy_medium.append(task.task_id)
        else:
            hard_extreme.append(task.task_id)
    return easy_medium, hard_extreme


def _remaining_time(started_at: float) -> float:
    return GLOBAL_BUDGET_SECONDS - (time.perf_counter() - started_at)


def main() -> int:
    input_root = Path(os.environ.get("INPUT_ROOT", "/input")).resolve()
    output_root = Path(os.environ.get("OUTPUT_ROOT", "/output")).resolve()
    logs_root = Path(os.environ.get("LOGS_ROOT", "/logs")).resolve()
    logs_root.mkdir(parents=True, exist_ok=True)

    config_path = Path(os.environ.get("CONFIG_PATH", "/app/configs/react_baseline.docker.yaml"))
    if not config_path.exists():
        config_path = Path("/app/configs/react_baseline.example.yaml")
    app_config = load_app_config(config_path)

    app_config = replace(
        app_config,
        dataset=replace(app_config.dataset, root_path=input_root),
        run=replace(app_config.run, output_dir=output_root),
    )

    dataset = DABenchPublicDataset(app_config.dataset.root_path)
    task_limit = _read_eval_limit()
    easy_medium_ids, hard_extreme_ids = _split_task_ids(dataset, limit=task_limit)
    ordered_task_ids = easy_medium_ids + hard_extreme_ids
    if not ordered_task_ids:
        print(f"No tasks found under {input_root}", file=sys.stderr)
        return 0

    shutdown_flag = threading.Event()
    started_at = time.perf_counter()
    workers = max(1, app_config.run.max_workers)

    def _mark_shutdown(sig: int, _frame: object) -> None:
        print(f"Signal {sig} received, preparing graceful shutdown...", file=sys.stderr)
        shutdown_flag.set()

    old_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _mark_shutdown)

    pending_hard_extreme = list(hard_extreme_ids)

    def _run_one(task_id: str) -> None:
        try:
            run_single_task(task_id=task_id, config=app_config, run_output_dir=output_root)
        except Exception as exc:  # noqa: BLE001
            err_path = output_root / task_id / "prediction.csv"
            _write_error_csv(err_path, f"ExecutionError: {exc}")
            print(f"[{task_id}] failed: {exc}", file=sys.stderr)

    try:
        if easy_medium_ids and not shutdown_flag.is_set():
            max_parallel = max(1, workers)
            with ThreadPoolExecutor(max_workers=max_parallel) as pool:
                in_flight: dict[Future[None], str] = {}
                to_submit = list(easy_medium_ids)

                while (to_submit or in_flight) and not shutdown_flag.is_set():
                    while to_submit and len(in_flight) < max_parallel and not shutdown_flag.is_set():
                        task_id = to_submit.pop(0)
                        in_flight[pool.submit(_run_one, task_id)] = task_id

                    if not in_flight:
                        break

                    done, _ = wait(in_flight.keys(), timeout=1.0, return_when=FIRST_COMPLETED)
                    for future in done:
                        in_flight.pop(future, None)
                        try:
                            future.result()
                        except Exception as exc:  # noqa: BLE001
                            print(f"Unexpected worker exception: {exc}", file=sys.stderr)

                    if _remaining_time(started_at) <= 0:
                        print("Global time budget reached while processing easy/medium tasks.", file=sys.stderr)
                        shutdown_flag.set()
                        break

        for task_id in pending_hard_extreme:
            if shutdown_flag.is_set():
                break
            if _remaining_time(started_at) <= 0:
                print("Global time budget reached before hard/extreme tasks.", file=sys.stderr)
                shutdown_flag.set()
                break
            _run_one(task_id)
    finally:
        signal.signal(signal.SIGTERM, old_handler)

    if shutdown_flag.is_set():
        remaining_ids = [
            task_id
            for task_id in ordered_task_ids
            if not (output_root / task_id / "prediction.csv").exists()
        ]
        for task_id in remaining_ids:
            _write_error_csv(
                output_root / task_id / "prediction.csv",
                "Execution interrupted before task start due to global timeout or shutdown signal.",
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
