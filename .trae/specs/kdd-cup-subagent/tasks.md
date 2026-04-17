# 任务拆解与实施计划

## 阶段一：定义 Subagent 的基础配置
1. **定义 Subagent 的 Prompt**：
   - 在 `src/data_agent_baseline/agents/prompt.py` 中新增 `DATA_ANALYST_SYSTEM_PROMPT`。
   - 定义子代理专门的结束工具（例如 `subagent_finish`）及其返回格式。
2. **设计 Subagent 运行参数**：
   - 创建 `SubagentConfig`，可以设置比主代理更小的 `max_steps` (如 8 步)，防止子代理陷入死循环过久。

## 阶段二：实现 Subagent 工具封装
1. **实现 Tool Handler**：
   - 在 `src/data_agent_baseline/tools/` 下新建 `subagent.py`，或在现有的工具文件中实现 `_delegate_to_data_analyst` 函数。
   - 在该函数内部，实例化一个新的 `LangGraphReActAgent` 或 `ReActAgent`。
   - 将主任务对象（`PublicTask`）与子任务描述（`sub_task_description`）结合，生成适合子代理的 `Task` 对象或初始输入。
2. **处理子代理的返回值**：
   - 提取子代理的最终输出或状态（`AgentRunResult`）。
   - 将其格式化为精简的字典，并封装在 `ToolExecutionResult` 中返回给主代理。

## 阶段三：注册与主代理集成
1. **注册工具**：
   - 在 `src/data_agent_baseline/tools/registry.py` 的 `create_default_tool_registry()` 中注册 `delegate_to_data_analyst` 工具及其 Schema。
2. **更新主代理 Prompt**：
   - 修改 `REACT_SYSTEM_PROMPT`，指导主代理在合适的时机使用 `delegate_to_data_analyst` 工具，并提供高质量的 `sub_task_description`。

## 阶段四：测试与验证
1. **单元测试**：
   - 编写针对 `_delegate_to_data_analyst` 的测试用例，确保传递的参数正确，并且状态是隔离的。
2. **集成测试**：
   - 使用真实的基准测试用例（如 KDD Cup 验证集），观察主代理是否能成功唤起子代理并正确处理其返回结果。
   - 验证子代理失败时，主代理是否能够优雅地接收错误并继续工作。
