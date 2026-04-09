# KDD Cup Data Agent Optimization Spec

## Why
当前项目结构已经具备了一个基于 LangGraph 的高级 Data Agent 雏形（包含 React 和 Plan-Execute 模式、工具集、上下文压缩等核心模块）。为了在 KDD Cup 中取得高分，我们需要针对比赛的特点（如：超大 Schema、复杂 SQL/Python 逻辑、严格的准确率要求和 Token 限制）对现有架构进行深度优化和增强。

## What Changes
**当前结构分析**：
- `agents/` & `orchestrators/`：已支持 React 和 Plan & Execute 双模式，这是处理复杂数据任务的良好基础。
- `runtime/` (包含 context_compression, recall)：已具备处理长上下文的中间件机制，对 KDD Cup 极为关键。
- `tools/`：内置了 DuckDB, SQLite, Python 执行环境。
- `benchmark/`：具备基础的评测框架雏形。

**提分向优化点 (Changes)**：
- [x] **架构评估**：完成现有基础架构的梳理。
- [ ] **增强 Context Management (上下文管理)**：优化 `context_compression.py`，针对超大数据库 Schema 进行智能截断和摘要提取；增强 `recall_provider.py` 以实现更精准的 Schema 检索 (RAG)。
- [ ] **增强 Tooling (工具链)**：在 `tools/` 中引入更高级的数据探索(EDA)工具，帮助 Agent 更好地理解数据分布。
- [ ] **引入 Reflection & Self-Correction (反思与自纠错)**：在 `agents/` 或 LangGraph 图中增加严格的执行错误反思节点，提升代码/SQL一次性通过率。
- [ ] **KDD Cup 专属 Benchmark 对齐**：优化 `benchmark/` 以完全对齐 KDD Cup 的打分逻辑（如执行准确率、结果比对），支持快速本地迭代打分。

## Impact
- Affected specs: Data Retrieval, Code Execution, Agent Reasoning, Evaluation.
- Affected code:
  - `src/data_agent_baseline/runtime/*`
  - `src/data_agent_baseline/tools/*`
  - `src/data_agent_baseline/orchestrators/*`
  - `src/data_agent_baseline/benchmark/*`

## ADDED Requirements
### Requirement: 智能 Schema RAG 与上下文压缩
The system SHALL provide 针对超大数据库的 Schema 检索能力，确保 Prompt 中只包含与当前 Query 相关的表结构，避免 Token 溢出，同时必须保证 `context_compression` 能有效处理过长的执行返回结果。

### Requirement: 执行错误反思与重试机制
The system SHALL provide 在 Python/SQL 执行报错时，自动将错误信息和 Traceback 反馈给 LLM 进行代码修正的能力，并具备严格的重试次数限制以防止死循环。

### Requirement: 高效的本地评估闭环
The system SHALL provide 完全模拟 KDD Cup 官方评分标准的本地 Benchmark 脚本，支持批量并发测试和错误案例归档，以指导进一步的 Prompt 优化。