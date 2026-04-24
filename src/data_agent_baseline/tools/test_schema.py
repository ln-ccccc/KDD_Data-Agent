import asyncio
from pathlib import Path
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.filesystem import get_data_schema
import json

class MockTask:
    def __init__(self, context_dir):
        self.context_dir = Path(context_dir)

task = MockTask("/workspace/demo_data/public/input/task_38/context")
res = get_data_schema(task, "csv/trans.csv")
print(json.dumps(res, indent=2))
