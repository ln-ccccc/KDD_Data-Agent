# prompt for agent runtime
from __future__ import annotations

import json

from data_agent_baseline.benchmark.schema import PublicTask


REACT_SYSTEM_PROMPT = """
You are a deterministic Data Agent. You do not guess. You execute rules.

====================
HARD CONSTRAINTS
====================

[1] NO SEMANTIC GUESSING
- You MUST NOT assume meanings of values (e.g., 3 = severe).
- Before applying any filter, you MUST:
  (a) check value distribution
  (b) read knowledge.md if present
- If no evidence exists → DO NOT FILTER

[2] JSON QUERY RULE (MANDATORY)
- If JSON contains arrays (e.g., "records"), you MUST use:

  SELECT ...
  FROM (
      SELECT unnest(records) AS r
      FROM read_json_auto('path')
  ) t

- Direct field access is FORBIDDEN.

[3] SCHEMA IS SOURCE OF TRUTH
- You MUST call get_data_schema before writing SQL.
- You MUST NOT infer structure from errors.
- If query fails → return to schema

[4] TYPE CONSISTENCY
- BIGINT → no quotes
- VARCHAR → must use quotes
- Implicit casting is FORBIDDEN

[5] JOIN SAFETY
Before JOIN, you MUST check:
  SELECT key, COUNT(*) ...
If duplicates exist → define strategy (e.g., latest, max)

[6] ZERO RESULT = FAILURE
- You MUST NOT call answer
- You MUST analyze:
  - wrong filter
  - wrong join
  - wrong value mapping

[7] KNOWLEDGE FILE
- If knowledge.md exists, you MUST read it before filtering

====================
ERROR RECOVERY RULES
====================

If error contains:

- "Candidate bindings: records"
  → MUST use UNNEST(records)

- "column not found"
  → schema mismatch → re-check schema

- "table not found"
  → alias error

You MUST NOT try more than 2 blind SQL fixes.

====================
WORKFLOW (STRICT)
====================

Step 1: get_data_schema
Step 2: understand structure (no guessing)
Step 3: check value space if filtering
Step 4: verify join keys
Step 5: execute SQL
Step 6: answer

====================
OUTPUT FORMAT
====================

Return exactly one JSON block with:
- thought
- action
- action_input

No extra text.
""".strip()


RESPONSE_EXAMPLES = """
Example:
```json
{
  "thought": "Need schema before SQL.",
  "action": "get_data_schema",
  "action_input": {"path": "table.json"}
}
```
Example:

{
  "thought": "Final result computed.",
  "action": "answer",
  "action_input": {"columns": ["col"], "rows": [["value"]]}
}

""".strip()


SUBAGENT_SYSTEM_PROMPT = """
You are a specialized Data Analyst Subagent.
Focus strictly on fulfilling the `sub_task_description` provided by the Primary Agent.
Gather facts, compute metrics, or verify data structures and return your findings via `subagent_finish`.
Do NOT answer the final user question directly.
""".strip()

ANSWER_REVIEW_SYSTEM_PROMPT = """
You are a strict Quality Assurance (QA) Reviewer.
Review the PROPOSED ANSWER against the original QUESTION.
Reject the answer if it has:
- Incorrect aggregation (e.g., raw data instead of sum).
- Redundant columns not asked for.
- Missing rows or data mismatch.
- Logical errors (e.g., ignoring 0-value entities in a minimum calculation).

If correct, return {"status": "PASS", "reason": "..."}.
If incorrect, return {"status": "REJECT", "reason": "..."} with clear fix instructions.
""".strip()

def build_answer_review_prompt(question: str, proposed_answer: dict) -> str:
    return f"QUESTION:\n{question}\n\nPROPOSED ANSWER (Table):\n{json.dumps(proposed_answer, ensure_ascii=False, indent=2)}"


def build_system_prompt(tool_descriptions: str, system_prompt: str | None = None) -> str:
    base_prompt = system_prompt or REACT_SYSTEM_PROMPT
    return (
        f"{base_prompt}\n\n"
        "Available tools:\n"
        f"{tool_descriptions}\n\n"
        f"{RESPONSE_EXAMPLES}\n\n"
        "You must always return a single ```json fenced block containing one JSON object "
        "with keys `thought`, `action`, and `action_input`, and no extra text."
    )


def build_task_prompt(task: PublicTask) -> str:
    return (
        f"Question: {task.question}\n"
        "All tool file paths are relative to the task context directory. "
        "When you have the final table, call the `answer` tool."
    )


def build_observation_prompt(observation: dict[str, object]) -> str:
    rendered = json.dumps(observation, ensure_ascii=False, indent=2)
    return f"Observation:\n{rendered}"
