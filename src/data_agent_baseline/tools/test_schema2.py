import json
import duckdb
from pathlib import Path

def test_schema(context_dir, relative_path):
    context_root = Path(context_dir).resolve()
    path = context_root / relative_path
    
    conn = duckdb.connect(':memory:', config={
        'home_directory': context_root.as_posix()
    })
    
    # In duckdb_exec.py we set file_search_path, let's pass absolute path to read_json_auto
    ext = path.suffix.lower()
    abs_path = path.as_posix()
    
    if ext == ".json":
        base_query = f"SELECT * FROM (SELECT unnest(records) as r FROM read_json_auto('{abs_path}'))"
        try:
            conn.execute(f"SELECT records FROM read_json_auto('{abs_path}') LIMIT 1")
        except Exception:
            base_query = f"SELECT * FROM '{abs_path}'"
    else:
        base_query = f"SELECT * FROM '{abs_path}'"
        
    sql = f"DESCRIBE {base_query}"
    cursor = conn.execute(sql)
    schema_rows = cursor.fetchall()
    
    enhanced_schema = []
    for row in schema_rows:
        col_name = row[0]
        col_type = row[1]
        
        distinct_values = []
        if "VARCHAR" in str(col_type).upper() or "INTEGER" in str(col_type).upper() or "BIGINT" in str(col_type).upper():
            safe_col_name = f'"{col_name}"' if not col_name.startswith('r.') else col_name
            if base_query.startswith("SELECT * FROM (SELECT unnest(records)"):
                val_sql = f"SELECT DISTINCT r.{safe_col_name} FROM (SELECT unnest(records) as r FROM read_json_auto('{abs_path}')) WHERE r.{safe_col_name} IS NOT NULL LIMIT 10"
            else:
                val_sql = f"SELECT DISTINCT {safe_col_name} FROM '{abs_path}' WHERE {safe_col_name} IS NOT NULL LIMIT 10"
                
            try:
                val_cursor = conn.execute(val_sql)
                distinct_values = [v[0] for v in val_cursor.fetchall()]
            except Exception as e:
                print(f"Error fetching values for {col_name}: {e}")
                
        enhanced_row = list(row)
        if distinct_values:
            formatted_vals = [str(v)[:50] + "..." if len(str(v)) > 50 else str(v) for v in distinct_values]
            enhanced_row.append(f"Sample values: {formatted_vals}")
        else:
            enhanced_row.append("")
            
        enhanced_schema.append(enhanced_row)
        
    print(json.dumps(enhanced_schema, indent=2))

test_schema("/workspace/demo_data/public/input/task_38/context", "csv/trans.csv")
