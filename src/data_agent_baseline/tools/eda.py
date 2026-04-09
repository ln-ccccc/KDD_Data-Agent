from __future__ import annotations

import io
from typing import Any
import pandas as pd
from pathlib import Path

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.filesystem import resolve_context_path

import numpy as np

def extract_dataframe_stats(task: PublicTask, relative_path: str) -> dict[str, Any]:
    """
    Extract statistical information from a data file (CSV, Parquet, JSON, Excel).
    Returns basic info like row/column count, head, describe(), missing values, etc.
    """
    path = resolve_context_path(task, relative_path)
    ext = path.suffix.lower()
    
    try:
        # For EDA, we might want to sample if the file is too large
        # But reading the whole file might be needed for describe()
        # To avoid OOM, let's try to read it normally, but limit rows for CSV/Excel
        if ext == ".csv":
            df = pd.read_csv(path, nrows=100000)
        elif ext == ".parquet":
            df = pd.read_parquet(path)
        elif ext in [".json", ".jsonl"]:
            df = pd.read_json(path, lines=(ext == ".jsonl"))
        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(path, nrows=100000)
        else:
            return {"error": f"Unsupported file format for EDA: {ext}", "success": False}
            
        # Basic info
        buf = io.StringIO()
        df.info(buf=buf)
        info_str = buf.getvalue()
        
        # Missing values
        missing_values = df.isnull().sum().to_dict()
        
        # Describe
        describe_df = df.describe(include='all')
        describe_dict = describe_df.replace({np.nan: None}).to_dict()
        
        # Head
        head_dict = df.head(5).replace({np.nan: None}).to_dict(orient="records")
        
        return {
            "path": relative_path,
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": list(df.columns),
            "info": info_str,
            "missing_values": missing_values,
            "describe": describe_dict,
            "head": head_dict,
            "success": True
        }
    except Exception as e:
        return {"error": str(e), "success": False}
