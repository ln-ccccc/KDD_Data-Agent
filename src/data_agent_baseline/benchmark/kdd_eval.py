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
    Compare two dataframes based on KDD Cup 2026 Column-As-Vector Matching rules.
    Returns True if every column in gold_df exists in pred_df (ignoring order of rows and column names).
    Extra columns in pred_df are allowed.
    """
    if pred_df.shape[0] != gold_df.shape[0]:
        return False

    pred_df = _normalize_dataframe(pred_df)
    gold_df = _normalize_dataframe(gold_df)

    matched_gold_cols = set()
    used_pred_cols = set()
    
    # 1. Standard Column-As-Vector matching (Order-independent)
    for g_col in gold_df.columns:
        g_vec = gold_df[g_col].sort_values().reset_index(drop=True)
        for p_col in pred_df.columns:
            if p_col in used_pred_cols:
                continue
            p_vec = pred_df[p_col].sort_values().reset_index(drop=True)
            try:
                pd.testing.assert_series_equal(
                    g_vec, 
                    p_vec, 
                    check_names=False, 
                    check_dtype=False, 
                    check_exact=False, 
                    atol=1e-5, 
                    rtol=1e-5
                )
                used_pred_cols.add(p_col)
                matched_gold_cols.add(g_col)
                break
            except AssertionError:
                continue

    # 2. Handle Name Splitting/Combining rule
    unmatched_gold = [c for c in gold_df.columns if c not in matched_gold_cols]
    unmatched_pred = [c for c in pred_df.columns if c not in used_pred_cols]
    
    if len(unmatched_gold) == 2 and len(unmatched_pred) >= 1:
        g_vec1 = gold_df[unmatched_gold[0]].astype(str)
        g_vec2 = gold_df[unmatched_gold[1]].astype(str)
        g_combined_1 = (g_vec1 + " " + g_vec2).sort_values().reset_index(drop=True)
        g_combined_2 = (g_vec2 + " " + g_vec1).sort_values().reset_index(drop=True)
        
        for p_col in unmatched_pred:
            p_vec = pred_df[p_col].astype(str).sort_values().reset_index(drop=True)
            try:
                pd.testing.assert_series_equal(g_combined_1, p_vec, check_names=False)
                matched_gold_cols.add(unmatched_gold[0])
                matched_gold_cols.add(unmatched_gold[1])
                used_pred_cols.add(p_col)
                break
            except AssertionError:
                pass
            try:
                pd.testing.assert_series_equal(g_combined_2, p_vec, check_names=False)
                matched_gold_cols.add(unmatched_gold[0])
                matched_gold_cols.add(unmatched_gold[1])
                used_pred_cols.add(p_col)
                break
            except AssertionError:
                pass

    return len(matched_gold_cols) == len(gold_df.columns)

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
