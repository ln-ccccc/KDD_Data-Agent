import asyncio
from data_agent_baseline.runtime.kernel.recall_provider import RecallProvider, RecallProviderConfig
from data_agent_baseline.runtime.kernel.state import RuntimeState, RuntimeStep, AbstractMemory
from data_agent_baseline.runtime.middlewares.context_compression import ContextCompressionMiddleware, ContextCompressionConfig
from data_agent_baseline.agents.model import ModelAdapter, ModelMessage

# Dummy Model
class DummyModel(ModelAdapter):
    def complete(self, messages: list[ModelMessage]) -> str:
        return "{}"

# Test 1.1: RecallProvider
state = RuntimeState(abstract=AbstractMemory())
state.steps.append(RuntimeStep(
    step_index=1, thought="", action="inspect_sqlite_schema", action_input={}, raw_response="",
    observation={
        "content": {
            "path": "test.db",
            "tables": [
                {"name": "users", "create_sql": "CREATE TABLE users (id int, name text)"},
                {"name": "orders", "create_sql": "CREATE TABLE orders (id int, user_id int, amount float)"},
                {"name": "products", "create_sql": "CREATE TABLE products (id int, desc text)"}
            ]
        }
    }, ok=True, llm_elapsed_seconds=0, tool_elapsed_seconds=0
))

rp = RecallProvider()
res = rp.recall(state=state, request={"kind": "sqlite_schema", "query": {"keywords": ["user"]}, "top_k": 1})
assert res["ok"], res
assert len(res["matches"]) == 1
assert len(res["matches"][0]["snippet"]["tables"]) == 2, res["matches"][0]["snippet"]["tables"]
assert res["matches"][0]["snippet"]["tables"][0]["name"] == "users"
assert res["matches"][0]["snippet"]["tables"][1]["name"] == "orders"
print("RecallProvider keyword filter works!")

# Test 1.2: ContextCompressionMiddleware
middleware = ContextCompressionMiddleware(model=DummyModel())
state_dict = {
    "steps": [
        {
            "action": "execute_python",
            "observation": {
                "output": "A" * 10000,
                "stderr": "B" * 5000
            }
        },
        {
            "action": "execute_context_sql",
            "observation": {
                "columns": ["id", "val"],
                "rows": [[1, "C" * 1000]] * 50
            }
        }
    ]
}

new_state = middleware.before_llm(state_dict)
new_steps = new_state["steps"]
obs1 = new_steps[0]["observation"]
assert len(obs1["output"]) < 6000, len(obs1["output"])
assert "Truncated" in obs1["output"]
assert len(obs1["stderr"]) < 6000, len(obs1["stderr"])
assert "Truncated" in obs1["stderr"]

obs2 = new_steps[1]["observation"]
assert len(obs2["rows"]) == 21, len(obs2["rows"]) # 10 head + 1 dummy + 10 tail
assert len(obs2["rows"][0][1]) < 200, len(obs2["rows"][0][1]) # String cells are truncated
assert "..." in obs2["rows"][0][1]

print("ContextCompressionMiddleware smart truncation works!")
