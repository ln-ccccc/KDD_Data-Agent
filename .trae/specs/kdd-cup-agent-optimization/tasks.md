# Tasks

- [x] Task 1: 优化上下文管理与 Schema RAG
  - [x] SubTask 1.1: 在 `runtime/recall_provider.py` 中实现基于向量检索或关键词的 Schema 过滤机制，处理超大数据库表。
  - [x] SubTask 1.2: 升级 `runtime/middlewares/context_compression.py`，针对过长的 SQL 或 Python 执行结果实现智能截断或摘要提取。
- [x] Task 2: 增强反思与自纠错机制 (Reflection)
  - [x] SubTask 2.1: 在 `orchestrators/langgraph/react_graph.py` 中强化 Error Handling 与 Reflection 节点，确保报错信息能无损回传。
  - [x] SubTask 2.2: 优化 `agents/prompt.py`，加入针对常见 KDD Cup 数据处理错误（如语法错误、Schema 不匹配）的系统提示词指导。
- [x] Task 3: 扩展提分工具链
  - [x] SubTask 3.1: 在 `tools/` 中新增高级数据探索工具 (如自动化的 dataframe 统计信息提取工具)，提升 Agent 对数据的先验理解。
- [x] Task 4: 对齐 KDD Cup 本地评测
  - [x] SubTask 4.1: 在 `benchmark/` 中实现完全模拟 KDD Cup 官方打分逻辑的自动化评测和对比脚本，输出可视化的得分报告。

# Task Dependencies
- [Task 2] depends on [Task 1]
- [Task 4] depends on [Task 1], [Task 2], [Task 3]