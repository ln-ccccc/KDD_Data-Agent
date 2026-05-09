## Summary

本计划修复两类会导致 task_22 / task_25 / task_80 等评测失败的关键问题：

1. DuckDB 在 schema 检查/查询阶段直接报错（`autoupdate` 配置项不兼容），导致 Agent 退化到 pandas 方案、丢失 SQL 语义与稳定性。
2. 选择逻辑默认“唯一最优解”（`idxmin()` / `LIMIT 1` / `iloc[0]` 等）导致并列最优（tie）时召回不完整；而 DataAgent-Bench 的 gold 结果大量是 set-based（返回所有满足最优条件的行）。

目标：让 Agent 在“最小/最大/最低/最高/最佳/最差”等最优化选择题中默认返回“所有并列最优”，并恢复 DuckDB schema/查询能力以减少非必要的 pandas workaround。

## Current State Analysis

### 1) DuckDB 配置导致工具链整体失效

- 现象：`get_data_schema` 与 `execute_duckdb_sql` 在多个任务中报错：
  - `Catalog Error: unrecognized configuration parameter "autoupdate"`
  - 例子见 [task_22 trace.json](file:///workspace/artifacts/runs/task_22/trace.json#L92-L116) 与 [task_80 trace.json](file:///workspace/artifacts/runs/task_80/trace.json#L92-L116)
- 根因：DuckDB 执行了不兼容版本的 `SET autoupdate=false;`：
  - [duckdb_exec.py](file:///workspace/src/data_agent_baseline/tools/duckdb_exec.py#L12-L52)（模块初始化）
  - [duckdb_exec.py](file:///workspace/src/data_agent_baseline/tools/duckdb_exec.py#L35-L56)（每次连接执行）
- 影响：
  - schema 获取失败 → prompt 规则“schema first / no blind guessing”无法落地
  - Agent 在任务中被迫使用 pandas 读文件 + 手工 join/aggregate，进一步放大 “selection collapse” 的概率

### 2) “排序问题”误写成“筛选问题”：tie 未处理

- 现象：task_25 的 pandas 计算使用：
  - `lowest_cost_event = event_costs.loc[event_costs['cost'].idxmin()]`
  - 见 [task_25 trace.json](file:///workspace/artifacts/runs/task_25/trace.json#L614-L656)
- 问题本质：
  - `idxmin()` / `LIMIT 1` / `head(1)` / `iloc[0]` 会把潜在的并列最优提前 collapse 成单行
  - gold 明确可能为多行 → 预测单行即使值对也会因 recall 不完整判错

### 3) QA Answer Review 的“行数推断”对 tie 不敏感

- LangGraph runtime 在提交答案前会做 QA review：
  - [react_graph.py:_answer_review_step](file:///workspace/src/data_agent_baseline/orchestrators/langgraph/react_graph.py#L347-L390)
- reviewer prompt 当前对“最小/最大类 selection”很可能推断为 1 行，从而放行单行答案：
  - [prompt.py:ANSWER_REVIEW_SYSTEM_PROMPT](file:///workspace/src/data_agent_baseline/agents/prompt.py#L96-L122)

## Proposed Changes

### A. 修复 DuckDB 工具链：移除不兼容 `autoupdate` 设置

**文件：** [duckdb_exec.py](file:///workspace/src/data_agent_baseline/tools/duckdb_exec.py)

- 删除或条件化执行以下语句，避免 DuckDB 版本不兼容直接使连接失败：
  - `_init_conn.execute("SET autoupdate=false;")`
  - `conn.execute("SET autoupdate=false;")`
- 保留现有的 extension directory / home directory 逻辑，以维持离线环境下的扩展加载策略。
- 验收标准：
  - `get_data_schema` 不再因 `autoupdate` 失败（至少对 CSV/JSON 能返回 DESCRIBE 结果）
  - `execute_duckdb_sql` 可执行基本 `SELECT 1` 与 `SELECT * FROM 'csv/xxx.csv' LIMIT 5`

### B. 强化“并列最优返回全部”的 Agent 行为约束（Prompt + QA）

#### B1) 主 Agent Prompt：显式禁止 collapse API，并给出正确模板

**文件：** [prompt.py:REACT_SYSTEM_PROMPT](file:///workspace/src/data_agent_baseline/agents/prompt.py#L9-L45)

- 在 CRITICAL RULES 与 Rules 中新增/强化一条 Tie-handling 规则（面向 SQL + pandas）：
  - 禁止默认使用：`idxmin()`、`idxmax()`、`nlargest(1)`、`nsmallest(1)`、`head(1)`、`LIMIT 1`、`iloc[0]`、`[0]` 来回答 “min/max/lowest/highest/cheapest/most expensive/earliest/latest” 等最优化选择题（除非题目明确要求返回一个且声明 tie-breaking 规则）。
  - 必须使用：先计算最优值，再用等值条件过滤，返回可能为多行的结果集。
- 推荐模板（将写入 prompt，供模型直接复制）：
  - **SQL（DuckDB）**：
    - 方案 1：窗口函数（更简洁）
      - `WITH agg AS (...) SELECT * FROM agg QUALIFY metric = MIN(metric) OVER ();`
    - 方案 2：子查询 MIN（兼容性更强）
      - `... HAVING SUM(cost) = (SELECT MIN(total_cost) FROM (...))`
  - **pandas**：
    - `min_v = df['metric'].min(); df[df['metric'] == min_v]`
    - 如果最终只需某一列：对过滤后的结果取 `.loc[:, ['col']].drop_duplicates()`（避免无意 `.iloc[0]`）

#### B2) QA Reviewer Prompt：把“min/max selection”视作“可能多行”

**文件：** [prompt.py:ANSWER_REVIEW_SYSTEM_PROMPT](file:///workspace/src/data_agent_baseline/agents/prompt.py#L96-L122)

- 更新 reviewer 的“Dynamic Shape Inference”规则：
  - 对包含 min/max/lowest/highest/best/worst/cheapest/most expensive/earliest/latest 等关键词的问题：
    - 允许答案为多行（tie 情况），不要以“语法单数”推断必须 1 行。
    - 若答案为 1 行，reviewer 应明确要求 Agent 做 tie-check（重新查询/重新计算：过滤 `== min` 后返回全部）。
  - 仅当问题明确说 “one / single / the first only / return one row” 或给出 tie-break（如 “if tie pick alphabetically smallest”）时，才强制 1 行。

### C. （可选但推荐）在 `answer` 工具侧增加“疑似 premature collapse”软拦截

**文件：** [registry.py:_answer](file:///workspace/src/data_agent_baseline/tools/registry.py#L142-L206)

目的：把 tie-handling 从“纯 prompt 约束”升级为“运行时安全网”，减少模型偶发违规。

- 在 `_answer` 里读取 `task.question`，做轻量关键词检测（min/max/lowest/highest/...）。
- 如果检测到是最优化选择题，且 `rows` 只有 1 行：
  - 返回 `ok=False` 的结构化错误（例如 `PotentialTieCollapse`），提示：
    - “请验证是否存在并列最优：先算 min/max，再用等值过滤返回所有行；不要 idxmin/LIMIT 1/iloc[0]”
  - 让 LangGraph 的 reflection 机制推动 Agent 自我修正并重新提交。
- 该拦截应是“软拦截”（只在极少数高风险模式触发），避免对明显的单值聚合题（sum/count/avg）造成误伤。

## Assumptions & Decisions

- 决策：对“最小/最大类最优化选择题”一律采用 set-based 默认语义（返回全部并列最优），这与用户观察到的 DataAgent-Bench gold 行为对齐。
- 决策：DuckDB 修复优先于 pandas workaround；schema/SQL 能用时尽量用 DuckDB 直接聚合/筛选，减少 pandas 手写逻辑分叉。
- 关键词检测（如果实现 C）仅作为 safeguard，不替代 prompt 与 QA reviewer；触发后以“要求 tie-check”方式纠偏。

## Verification Steps

### 1) DuckDB 连接与 schema 获取冒烟测试

- 在本仓库环境中运行一段最小验证：
  - `execute_duckdb_sql(context_root, "SELECT 1")` 返回 success=true
  - 对任意已有 run 的 context（例如 task_80）执行 `SELECT * FROM 'csv/qualifying.csv' LIMIT 5` 成功
- 预期：不再出现 `unrecognized configuration parameter "autoupdate"`。

### 2) 行为回归：并列最优能返回多行

- 在公开数据集上重跑（至少）task_25，并对照 gold：
  - `uv run dabench run-task task_25 --config configs/react_baseline.example.yaml`
  - 预期：prediction.csv 行数与 gold.csv 一致（多行并列最优时不截断）
- 建议也重跑 task_22 / task_80，确认 schema 工具恢复后 Agent 不再退化到不稳定 workaround：
  - `uv run dabench run-task task_22 ...`
  - `uv run dabench run-task task_80 ...`

### 3) 评测侧校验（可选）

- 对新的 run 目录执行：
  - [kdd_eval.py](file:///workspace/src/data_agent_baseline/benchmark/kdd_eval.py) 的 compare/evaluate 流程，确认行数不匹配类错误显著下降。

