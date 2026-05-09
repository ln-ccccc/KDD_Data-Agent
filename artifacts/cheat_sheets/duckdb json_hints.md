

### Learned from Task task_11
- **Error Pattern:** Binder Error: Table "p" does not have a column named "ID" when querying JSON files directly with DuckDB
- **Solution:** SELECT * FROM (SELECT unnest(records) as r FROM read_json_auto('file.json'))
- **Reasoning:** DuckDB reads JSON files as a single table with a 'records' column. To access the nested fields, you need to unnest this 'records' array.


### Learned from Task task_24
- **Error Pattern:** Binder Error: Referenced column "column_name" not found in FROM clause! Candidate bindings: "records"
- **Solution:** When querying JSON files with DuckDB, if the columns are not directly accessible, it often means the JSON is an array of objects. Use `SELECT * FROM read_json_auto('file.json')` to treat the JSON as a table. If the JSON has a root key like 'records' containing an array, use `SELECT * FROM (SELECT unnest(records) as r FROM read_json_auto('file.json'))` and then access columns as `r.column_name`.
- **Reasoning:** DuckDB's `read_json_auto` can sometimes infer the schema, but for nested structures or arrays, explicit unnesting might be required. The error indicates that the top-level keys are not directly the column names I'm looking for.
