import json
import logging
from pathlib import Path

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize dataframe for comparison."""
    # Convert column names to lowercase and strip
    df.columns = [str(c).lower().strip() for c in df.columns]
    
    # Sort columns alphabetically
    df = df[sorted(df.columns)]
    
    # Trim whitespace from string columns and handle empty strings
    for col in df.columns:
        if df[col].dtype == "object":
            try:
                df[col] = df[col].str.strip()
                df[col] = df[col].replace("", np.nan)
            except Exception:
                pass

    # Sort rows by all columns
    try:
        df = df.sort_values(by=list(df.columns)).reset_index(drop=True)
    except Exception as e:
        logger.debug(f"Failed to sort dataframe: {e}")
    return df

def compare_dataframes(pred_df: pd.DataFrame, gold_df: pd.DataFrame) -> bool:
    """
    Compare two dataframes. Returns True if they match.
    Matches ignore column/row order and use a float tolerance.
    """
    if pred_df.shape != gold_df.shape:
        return False
        
    pred_df = _normalize_dataframe(pred_df)
    gold_df = _normalize_dataframe(gold_df)
    
    if list(pred_df.columns) != list(gold_df.columns):
        return False
        
    try:
        pd.testing.assert_frame_equal(
            pred_df, 
            gold_df, 
            check_dtype=False,
            check_column_type=False,
            check_exact=False,
            atol=1e-5,
            rtol=1e-5
        )
        return True
    except AssertionError:
        return False

def evaluate_run(run_dir: Path, gold_dir: Path, output_report: Path | None = None) -> dict:
    """
    Evaluate a complete run against the gold dataset.
    Returns a dictionary with evaluation results.
    """
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing summary.json in {run_dir}")
        
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
        
    tasks = summary.get("tasks", [])
    
    results = []
    correct_count = 0
    total_count = len(tasks)
    
    for task in tasks:
        task_id = task["task_id"]
        pred_csv = run_dir / task_id / "prediction.csv"
        gold_csv = gold_dir / task_id / "gold.csv"
        
        status = "Failed"
        reason = ""
        is_correct = False
        
        if not task.get("succeeded", False):
            status = "Execution Error"
            reason = task.get("failure_reason", "Unknown error")
        elif not pred_csv.exists():
            status = "Missing Prediction"
            reason = "prediction.csv not found"
        elif not gold_csv.exists():
            status = "Missing Gold"
            reason = "gold.csv not found in dataset"
        else:
            try:
                pred_df = pd.read_csv(pred_csv)
                gold_df = pd.read_csv(gold_csv)
                if compare_dataframes(pred_df, gold_df):
                    is_correct = True
                    status = "Correct"
                    correct_count += 1
                else:
                    status = "Wrong Answer"
                    reason = "Data mismatch"
            except Exception as e:
                status = "Evaluation Error"
                reason = str(e)
                
        results.append({
            "task_id": task_id,
            "status": status,
            "reason": reason,
            "is_correct": is_correct
        })
        
    pass_rate = correct_count / total_count if total_count > 0 else 0.0
    
    report = {
        "run_id": summary.get("run_id", "unknown"),
        "total_tasks": total_count,
        "correct_tasks": correct_count,
        "pass_rate": pass_rate,
        "results": results
    }
    
    if output_report:
        with open(output_report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            
    return report

def print_visual_report(report: dict, console: Console | None = None):
    """
    Print a visual report to the console using rich.
    """
    if console is None:
        console = Console()
        
    console.print(f"\n[bold]Evaluation Report: {report['run_id']}[/bold]")
    console.print(f"Total Tasks: {report['total_tasks']}")
    console.print(f"Correct: [green]{report['correct_tasks']}[/green]")
    console.print(f"Pass Rate: [bold cyan]{report['pass_rate']:.2%}[/bold cyan]\n")
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Task ID", style="dim")
    table.add_column("Status")
    table.add_column("Reason")
    
    for res in report["results"]:
        status = res["status"]
        if status == "Correct":
            status_str = f"[green]{status}[/green]"
        elif status == "Wrong Answer":
            status_str = f"[red]{status}[/red]"
        else:
            status_str = f"[yellow]{status}[/yellow]"
            
        table.add_row(res["task_id"], status_str, str(res["reason"])[:50])
        
    console.print(table)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Evaluate KDD Cup Data Agent run")
    parser.add_argument("--run-dir", type=Path, required=True, help="Path to the run directory (e.g. artifacts/runs/run_xxx)")
    parser.add_argument("--gold-dir", type=Path, default=Path("data/public/output"), help="Path to the gold output directory")
    parser.add_argument("--output-report", type=Path, default=None, help="Path to save the evaluation JSON report")
    args = parser.parse_args()
    
    report = evaluate_run(args.run_dir, args.gold_dir, args.output_report)
    print_visual_report(report)
