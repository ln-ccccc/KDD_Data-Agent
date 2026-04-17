# DuckDB JSON Array Unnesting Pattern

When working with JSON files in DuckDB, you will frequently encounter deeply nested structures or arrays. 
DuckDB provides powerful tools to handle these, but you MUST use the correct syntax to avoid parsing errors.

## 1. The `read_json_auto` Function
Always use `read_json_auto('path/to/file.json')` to automatically infer the schema of the JSON file. Do NOT try to read it as a raw text string.

## 2. Flattening Arrays (UNNEST)
If the root of the JSON is an object that contains an array (e.g., `{"records": [{"id": 1}, {"id": 2}]}`), you cannot simply `SELECT records FROM ...`. You must unnest it to treat each element as a row.

### WRONG (Will return a single row with a list):
```sql
SELECT records FROM read_json_auto('data.json')
```

### CORRECT (Returns multiple rows):
```sql
SELECT unnest(records) as record FROM read_json_auto('data.json')
```

## 3. Accessing Fields inside Structs
Once you have unnested the array into a struct (like the `record` alias above), you can access its fields using dot notation `.` or bracket notation `['field_name']`.

### CORRECT (Extracting specific fields):
```sql
SELECT 
    record.id,
    record.user.name,
    record.amount
FROM (
    SELECT unnest(records) as record FROM read_json_auto('data.json')
)
```

## 4. Complex Example: Flattening and Filtering
If you need to find the total amount spent by "Alice" from a JSON array of transactions:
```sql
SELECT 
    SUM(record.amount) as total_spent
FROM (
    SELECT unnest(transactions) as record FROM read_json_auto('purchases.json')
)
WHERE record.user.name = 'Alice'
```

## Summary
Whenever you see a JSON array, think `UNNEST()`. This is the ONLY reliable way to transform nested JSON lists into queryable relational rows in DuckDB.