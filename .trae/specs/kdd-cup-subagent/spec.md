# KDD Cup Subagent (Data Analyst) 架构规范

## 1. 概述
在 `data_agent_baseline` 的现有架构中，引入 Subagent（子代理）模式，旨在将复杂的专业任务（如深度数据探索、复杂 SQL 编写与调试、高级特征工程等）从主代理（Primary Agent）中剥离。
主代理充当规划者（Planner）或通用代理，而子代理（如 Data Analyst Subagent）充当特定领域的专家。子代理将被封装为一个标准的工具（Tool），注册到主代理的 `ToolRegistry` 中供其调用。

## 2. 核心架构设计

### 2.1 模式（Pattern）
主代理（Primary Agent） -> 调用 `delegate_to_data_analyst` 工具 -> 实例化并运行子代理（Subagent） -> 返回结构化结果给主代理的观察（Observation）。

### 2.2 Prompt 设计 (Prompt Design)
- **主代理 (Primary Agent)**：需要在 `REACT_SYSTEM_PROMPT` 中增加指导原则，明确何时以及如何将任务委托给数据分析子代理。例如：“遇到需要多步探索的数据分析任务，或者复杂的 SQL 构建任务时，请调用 `delegate_to_data_analyst` 工具，并提供详细的子任务描述。”
- **子代理 (Data Analyst Subagent)**：拥有独立的 `SUBAGENT_SYSTEM_PROMPT`。该 Prompt 需要强调其“数据分析专家”的角色，专注于执行具体的数据操作指令，并最终通过一个特定的工具（例如 `subagent_finish` 或直接返回结果）向主代理汇报执行结果和发现，而不是直接回答最终的用户问题。

### 2.3 工具注册 (Tool Registration)
- **封装形式**：创建一个新的工具处理函数（Tool Handler），例如 `_delegate_to_data_analyst(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult`。
- **输入 Schema (Input Schema)**：
  ```json
  {
    "sub_task_description": "详细描述子代理需要完成的具体任务",
    "focus_files": ["需要重点关注的文件路径列表"]
  }
  ```
- **输出 Schema (Output Schema)**：
  作为 `ToolExecutionResult.content` 返回，通常包含：
  ```json
  {
    "status": "success | failure",
    "summary": "子代理执行任务的总结和关键发现",
    "data_references": ["产生的新文件路径", "相关数据视图"]
  }
  ```
- **注册过程**：在 `tools/registry.py` 的 `create_default_tool_registry()` 中注册该工具。

### 2.4 状态隔离策略 (State Isolation Strategy)
为了避免子代理的思考过程（Thought/Action）污染主代理的上下文（Context）和状态（State），必须实施严格的状态隔离：
1. **独立的 RuntimeState**：子代理的运行必须实例化一个新的 `LangGraphReActAgent` 或 `ReActAgent`，并拥有自己独立的 `RuntimeState` 实例。子代理的 `steps`、`abstract` (Memory) 和 `prompt_window_steps` 与主代理完全物理隔离。
2. **上下文控制**：子代理的生命周期仅限于单次工具调用内。执行完毕后，其详细的 `steps` 会被总结或丢弃，只向主代理返回关键的摘要信息（Summary）和必要的数据（Data）。
3. **错误隔离**：如果子代理内部发生崩溃或达到最大步数（`max_steps`），该错误应被捕获并转化为一条包含错误信息的 `ToolExecutionResult` 返回给主代理，而不是导致主代理直接崩溃。主代理可以根据这个失败的观察（Observation）决定重试或采取其他策略。
4. **工具集隔离 (可选)**：可以为子代理配置一个子集或专用的 `ToolRegistry`。例如，数据分析子代理可能只需要数据探索和 SQL 执行工具，不需要再次调用自身或其他规划类工具。
