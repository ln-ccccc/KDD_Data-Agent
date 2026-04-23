import json
import os
from pathlib import Path
from collections import defaultdict

def analyze_traces(run_dir):
    errors = defaultdict(list)
    task_dirs = list(Path(run_dir).glob("task_*"))
    
    total = len(task_dirs)
    failed = 0
    
    for t_dir in task_dirs:
        trace_path = t_dir / "trace.json"
        if not trace_path.exists():
            continue
            
        with open(trace_path) as f:
            trace = json.load(f)
            
        if trace.get("succeeded"):
            continue
            
        failed += 1
        
        last_error = None
        for step in trace.get("steps", []):
            obs = step.get("observation", {})
            if obs and not obs.get("ok"):
                content = obs.get("content", {})
                if isinstance(content, dict) and "error" in content:
                    last_error = content["error"]
                elif isinstance(content, str):
                    last_error = content
        
        failure_reason = trace.get("failure_reason", "Unknown")
        if last_error:
            short_err = last_error.split('\n')[0][:100]
            errors[short_err].append(t_dir.name)
        else:
            errors[failure_reason].append(t_dir.name)
            
    print(f"Total tasks processed: {total}")
    print(f"Failed tasks: {failed}")
    
    for err, tasks in sorted(errors.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"\n[{len(tasks)} tasks] {err}")
        print(f"  Tasks: {', '.join(tasks[:5])}{'...' if len(tasks)>5 else ''}")

if __name__ == "__main__":
    analyze_traces("artifacts/runs/duckdb_test_all_v2")
