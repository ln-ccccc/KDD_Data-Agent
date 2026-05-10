# DataAgent-Bench 项目架构规范 (spec.md)

本项目是专为 KDD Cup 2026 DataAgent-Bench 挑战赛设计的官方 Starter Kit。它采用高度模块化的架构，旨在支持数据智能代理（Data Agent）在处理复杂数据任务（如 CSV, JSON, SQLite, 文档等）时的规划、执行、反思与自我修复。

---

### **项目概述**

DataAgent-Bench 核心目标是构建能够自主探索异构数据源并回答复杂问题的智能体。系统采用 **ReAct** (Reasoning and Acting) 模式，并结合 **Plan-and-Execute** 混合架构，利用 **LangGraph** 进行工作流编排，确保代理在长程任务中的稳定性和可解释性。

---

### **核心模块职责**

#### **1. Core (核心基础层)**
- **位置**: [core/](file:///workspace/src/data_agent_baseline/core)
- **职责**:
    - **类型定义 ([types.py](file:///workspace/src/data_agent_baseline/core/types.py))**: 定义跨模块共享的基础数据结构，如 `RetryPolicy`（重试策略）、`TraceLevel`（追踪等级）等。
    - **异常管理 ([errors.py](file:///workspace/src/data_agent_baseline/core/errors.py))**: 统一项目内的错误处理逻辑，确保工程化分层的稳健性。
    - **解耦设计**: 作为最底层模块，不依赖其他业务逻辑，有效减少循环依赖。

#### **2. Runtime (运行时内核)**
- **位置**: [runtime/](file:///workspace/src/data_agent_baseline/runtime)
- **职责**:
    - **状态管理 ([state.py](file:///workspace/src/data_agent_baseline/runtime/kernel/state.py))**: 定义 `RuntimeState` 作为单源事实（SSOT），驱动 LangGraph 状态机。
    - **记忆系统 ([memory.py](file:///workspace/src/data_agent_baseline/runtime/kernel/memory.py))**: 维护 `AbstractMemory`，负责从执行历史中提取事实、管理资产清单及更新待办事项。
    - **上下文策略 ([policy.py](file:///workspace/src/data_agent_baseline/runtime/kernel/policy.py))**: 智能调度中心，决定何时触发上下文压缩、RAG 检索或更新抽象记忆，以应对 LLM 上下文窗口限制。
    - **数据还原 ([reducer.py](file:///workspace/src/data_agent_baseline/runtime/kernel/reducer.py))**: 将 LLM 的原始输出和工具执行结果还原为结构化的状态更新。
    - **中间件层 ([middlewares/](file:///workspace/src/data_agent_baseline/runtime/middlewares))**: 实现如上下文压缩 ([context_compression.py](file:///workspace/src/data_agent_baseline/runtime/middlewares/context_compression.py)) 等横切关注点。

#### **3. Agents (代理逻辑层)**
- **位置**: [agents/](file:///workspace/src/data_agent_baseline/agents)
- **职责**:
    - **模型适配 ([model.py](file:///workspace/src/data_agent_baseline/agents/model.py))**: 封装 OpenAI 兼容的 API 调用，支持多种 LLM 后端。
    - **执行模式**: 
        - [react.py](file:///workspace/src/data_agent_baseline/agents/react.py): 实现基础的思考-行动-观察循环。
        - [plan_execute.py](file:///workspace/src/data_agent_baseline/agents/plan_execute.py): 实现“先规划、后执行”的混合模式，提高复杂任务的成功率。
    - **提示词工程 ([prompt.py](file:///workspace/src/data_agent_baseline/agents/prompt.py))**: 集中管理 System Prompt 和各阶段的任务模版。

#### **4. Tools (工具能力集)**
- **位置**: [tools/](file:///workspace/src/data_agent_baseline/tools)
- **职责**:
    - **数据交互**: 包含 [filesystem.py](file:///workspace/src/data_agent_baseline/tools/filesystem.py)（文件预览）、[sqlite.py](file:///workspace/src/data_agent_baseline/tools/sqlite.py) / [duckdb_exec.py](file:///workspace/src/data_agent_baseline/tools/duckdb_exec.py)（SQL 查询）和 [python_exec.py](file:///workspace/src/data_agent_baseline/tools/python_exec.py)（沙箱代码执行）。
    - **检索增强 (RAG)**: [knowledge_retriever.py](file:///workspace/src/data_agent_baseline/tools/knowledge_retriever.py)（文档检索）和 [schema_retriever.py](file:///workspace/src/data_agent_baseline/tools/schema_retriever.py)（数据库模式检索）。
    - **多智能体协作**: [subagent.py](file:///workspace/src/data_agent_baseline/tools/subagent.py) 支持将复杂子任务委托给专门的子代理。
    - **工具注册 ([registry.py](file:///workspace/src/data_agent_baseline/tools/registry.py))**: 统一管理工具的 Schema 定义和执行句柄。
    - **技能扩展 ([skills/](file:///workspace/src/data_agent_baseline/tools/skills))**: 动态加载特定的 Python 技能脚本。

#### **5. Orchestrators (编排层)**
- **位置**: [orchestrators/](file:///workspace/src/data_agent_baseline/orchestrators)
- **职责**:
    - **LangGraph 集成**: 利用图结构定义 Agent 的执行流，包括 LLM 节点、Tool 节点、Reflection（反思）节点和 Answer Review（答案审核）节点。
    - **控制流管理**: 处理节点间的条件跳转，实现自动反思、错误重试和多回合对话。

#### **6. Benchmark (评测与数据层)**
- **位置**: [benchmark/](file:///workspace/src/data_agent_baseline/benchmark)
- **职责**:
    - **数据加载 ([dataset.py](file:///workspace/src/data_agent_baseline/benchmark/dataset.py))**: 负责读取 `task.json` 和 `context/` 目录下的各类数据资产。
    - **数据结构 ([schema.py](file:///workspace/src/data_agent_baseline/benchmark/schema.py))**: 定义任务、资产和最终答案表（`AnswerTable`）的标准格式。
    - **评分逻辑 ([kdd_eval.py](file:///workspace/src/data_agent_baseline/benchmark/kdd_eval.py))**: 实现与比赛标准一致的自动评测逻辑。

#### **7. Run (执行入口)**
- **位置**: [run/](file:///workspace/src/data_agent_baseline/run)
- **职责**:
    - **运行器 ([runner.py](file:///workspace/src/data_agent_baseline/run/runner.py))**: 串联数据集、Agent 和输出目录，负责单任务运行或多线程批量 Benchmark 运行。
    - **产物持久化**: 将 `trace.json`（执行轨迹）和 `prediction.csv`（预测结果）写出至 `artifacts/` 目录。

---

### **关键工作流**

1.  **初始化**: `Runner` 加载配置和任务数据。
2.  **规划**: `PlanExecuteAgent` 生成高层执行计划。
3.  **循环 (ReAct)**: 
    - `Renderer` 根据当前状态生成 Prompt。
    - `LLM` 输出 Action。
    - `ToolRegistry` 执行 Action 并返回 Observation。
    - `Policy` 监控上下文长度，必要时触发上下文压缩或事实提取。
4.  **反思与修正**: 若工具执行失败，`Reflection` 节点介入提供错误分析。
5.  **审核**: `AnswerReview` 节点对生成的答案进行语义校验。
6.  **交付**: 提交最终 `AnswerTable` 并持久化运行日志。

---

### **技术栈**

- **语言**: Python 3.10+
- **包管理**: [uv](https://docs.astral.sh/uv/)
- **工作流编排**: [LangGraph](https://www.langchain.com/langgraph)
- **数据处理**: Pandas, Polars, DuckDB, PyArrow
- **模型交互**: OpenAI SDK, SentenceTransformers (Embedding)
- **配置与 CLI**: PyYAML, Typer, Rich
