# KDD Cup Data Agent Baseline - 架构优化与演进总结

基于“比赛提分优先、适度防御”的原则，我们对官方 Starter Kit 的 Baseline 进行了深度的改造和健壮性提升。以下是本次演进的核心成果与架构变更：

## 1. 核心能力引入：DuckDB 引擎
**背景**：大模型编写复杂 Python pandas 脚本的数据分析准确率往往不如纯 SQL。
**改造**：
- 新增 `execute_duckdb_sql` 工具 ([duckdb_exec.py](file:///Users/dp/agent/kddcup2026-data-agents-starter-kit/src/data_agent_baseline/tools/duckdb_exec.py))。
- 允许大模型直接对本地沙箱中的 CSV、JSON 和 Parquet 文件执行原生 SQL 查询（例如：`SELECT * FROM 'data.csv'`）。
- **权限与隔离**：通过 `file_search_path`、`extension_directory` 和 `home_directory` 限制 DuckDB 的运行上下文，确保其无法越权读取沙箱外数据，同时解决了动态下载扩展（如 `json`, `sqlite`）时的权限报错问题。

## 2. 自动化错题本与自省机制 (Auto-Reflection & Cheat Sheets)
**背景**：DuckDB 处理复杂 JSON 数组或跨库关联时有特定的语法陷阱（如 `read_json_auto`、`maximum_object_size` 等），大模型容易陷入无休止的语法试错并耗尽 16 步（max_steps）。
**改造**：
- **放弃规则硬编码兜底**：坚守 Agent 哲学，不在 Python 底层用正则强行改写模型的 SQL。
- **构建 Cheat Sheets 体系**：在 `artifacts/cheat_sheets/` 下建立了按文件类型划分的知识库（如 `json_hints.md`, `sqlite_hints.md`）。
- **工具级动态注入**：在文件预览工具（如 `read_json`, `inspect_sqlite_schema`）被调用时，自动将对应的错题集作为 `[Your Previous Learnings]` 强塞给大模型的 `observation`，实现防呆。
- **自我学习闭环**：新增 `update_cheat_sheet` 工具并在 System Prompt 强制要求：大模型在连续犯错并最终成功后，必须调用该工具将成功的经验记录到对应的 `.md` 中，实现跨 Task 的经验传承。

## 3. 增强的系统级鲁棒性与可观测性
**背景**：模型 API 抽风返回空内容，或父进程超时导致 trace 文件为空，难以排查死因。
**改造**：
- **API 层重试机制 (Retry Loop)**：在 [model.py](file:///Users/dp/agent/kddcup2026-data-agents-starter-kit/src/data_agent_baseline/agents/model.py) 中，如果模型返回 `content=None`（非安全拦截的无故罢工），系统会自动追加 `user` 提示要求重试，最大重试 2 次。这挽救了如 `task_145` 等偶发失败的任务。
- **请求级超时熔断**：为 `client.chat.completions.create` 增加 `timeout=90.0`，防止单次模型推理卡死拖垮 600s 的全局超时。
- **防空白 Trace**：在 `runner.py` 子进程增加启动心跳。如果全局超时导致进程被强制 kill，父进程会自动伪造一条包含超时原因的 `steps` 写入 `trace.json`。
- **调试开关**：引入环境变量 `ENABLE_MODEL_DEBUG=1`，开启后可在终端实时观测模型的 `finish_reason`、返回类型及 `tool_calls` 明细。

## 4. 验证与成果
在开启所有修复与自学习机制后，我们在全量 50 个 Task 的 Benchmark 中观察到：
1. `task_64` (大 JSON 嵌套查询) 完美避坑，一次性写对 `read_json_auto` 与 `UNNEST`。
2. `task_145` (多源 SQLite+CSV 联合查询) 避开了不支持的 `sqlite_scan` 相对路径陷阱，主动降级为分步查询与关联。
3. 整体工具链的稳定性达到极高水平，未出现任何因解析器本身缺陷导致的 Crash。

---
*Date: 2026-04-07*