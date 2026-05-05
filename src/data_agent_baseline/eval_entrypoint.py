from __future__ import annotations

import csv
import os
import sys
from dataclasses import replace
from pathlib import Path

from data_agent_baseline.benchmark.dataset import DABenchPublicDataset
from data_agent_baseline.config import DatasetConfig, RunConfig, load_app_config
from data_agent_baseline.run.runner import run_single_task


def _write_error_csv(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Error"])
        writer.writerow([message[:1000]])


def main() -> int:
    input_root = Path(os.environ.get("INPUT_ROOT", "/input")).resolve()
    output_root = Path(os.environ.get("OUTPUT_ROOT", "/output")).resolve()
    logs_root = Path(os.environ.get("LOGS_ROOT", "/logs")).resolve()
    logs_root.mkdir(parents=True, exist_ok=True)

    config_path = Path(os.environ.get("CONFIG_PATH", "/app/configs/react_baseline.docker.yaml"))
    if not config_path.exists():
        config_path = Path("/app/configs/react_baseline.example.yaml")
    app_config = load_app_config(config_path)

    dataset_config = replace(app_config.dataset, root_path=input_root)
    run_config = replace(app_config.run, output_dir=output_root)
    app_config = replace(app_config, dataset=dataset_config, run=run_config)

    dataset = DABenchPublicDataset(app_config.dataset.root_path)
    task_ids = dataset.list_task_ids()
    if not task_ids:
        print(f"No tasks found under {input_root}", file=sys.stderr)
        return 0

    for task_id in task_ids:
        try:
            run_single_task(task_id=task_id, config=app_config, run_output_dir=output_root)
        except Exception as exc:  # noqa: BLE001
            err_path = output_root / task_id / "prediction.csv"
            _write_error_csv(err_path, f"ExecutionError: {exc}")
            print(f"[{task_id}] failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

