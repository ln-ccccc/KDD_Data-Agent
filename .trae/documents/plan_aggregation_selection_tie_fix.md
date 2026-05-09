## Summary

本计划修复并**强约束**两类会持续导致 task_22 / task_25 / task_80 等评测失败的关键问题：

1. DuckDB 在 schema 检查/查询阶段直接报错（`autoupdate` 配置项不兼容），导致 Agent 被迫 fallback 到 pandas，并在 fallback 分支里大量出现“排序 + 截断”（`idxmin()` / `iloc[0]`）等不完整逻辑。
2. 更根本的问题：Agent 的决策路径默认把最优化问题当作“选一个最优项”（unique argmin / unique argmax），而 DataAgent-Bench 的真实语义大量是 **set-based**：需要返回所有并列最优（ties）。

目标（可执行验收）：无论 DuckDB 是否可用，Agent 在“min/max/lowest/highest/best/worst/earliest/latest”等最优化题型上必须走同一条 canonical 路径：**先算全局最优值，再做等值过滤返回全部行**，并且工具层/QA 层能够让“排序 + 截断”策略无法被放行。

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
  - 重要：**DuckDB 修好并不自动修复逻辑**。否则错误会从 pandas 版 `idxmin()` 迁移成 SQL 版 `ORDER BY ... LIMIT 1`。

### 2) “排序问题”误写成“筛选问题”：tie 未处理

- 现象：task_25 的 pandas 计算使用：
  - `lowest_cost_event = event_costs.loc[event_costs['cost'].idxmin()]`
  - 见 [task_25 trace.json](file:///workspace/artifacts/runs/task_25/trace.json#L614-L656)
- 问题本质：
  - 任何“排序 + 截断”的最优解获取方式（包括但不限于 `idxmin()` / `idxmax()` / `nsmallest(1)` / `nlargest(1)` / `head(1)` / `iloc[0]` / `ORDER BY ... LIMIT 1`）都会把潜在的并列最优提前 collapse 成单行
  - gold 明确可能为多行 → 预测单行即使值对也会因 recall 不完整判错

### 3) QA Answer Review 的“行数推断”对 tie 不敏感

- LangGraph runtime 在提交答案前会做 QA review：
  - [react_graph.py:_answer_review_step](file:///workspace/src/data_agent_baseline/orchestrators/langgraph/react_graph.py#L347-L390)
- reviewer prompt 当前对“最小/最大类 selection”很可能推断为 1 行，从而放行单行答案（这不是“没考虑 tie”，而是 reviewer 缺少强制验证分支）：
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

### A2. DuckDB JSON 读取模式收敛（避免 “schema OK 但 query 炸”）

**文件：** [filesystem.py:get_data_schema](file:///workspace/src/data_agent_baseline/tools/filesystem.py#L97-L179) 与 [duckdb_json_array.md](file:///workspace/src/data_agent_baseline/tools/skills/duckdb_json_array.md)

- 现状：`get_data_schema` 对 JSON 采用 `read_json_auto(...)` + `unnest(records)` 的试探式分支，DuckDB 版本差异下存在不稳定风险。
- 计划：统一为“明确语义、可 fallback”的 JSON 读取策略（工具层内置，不依赖 LLM 记忆）：
  1. 首选：`read_json_auto`（保持自动推断优势）
  2. 若失败：fallback 到 `read_json`（显式 JSON 读取）
  3. 对 `{"records":[...]}` 结构：统一用 `SELECT unnest(records) AS r FROM ...` 再 `SELECT r.* ...`
- 验收标准：task_22 / task_80 这类 JSON+CSV 任务，schema 探测与 DuckDB 查询不再出现 JSON 解析类随机失败。

### B. 强化“并列最优返回全部”的 Agent 行为约束（Hard constraint + Canonical operator）

#### B1) 主 Agent Prompt：升级为 HARD CONSTRAINT（违反即视为 reasoning error）

**文件：** [prompt.py:REACT_SYSTEM_PROMPT](file:///workspace/src/data_agent_baseline/agents/prompt.py#L9-L45)

- 新增一个明确分节：`### HARD CONSTRAINT (FAIL IF VIOLATED)`，内容包含：
  - 对所有最优化题（min/max/lowest/highest/best/worst/earliest/latest 等）：
    - 任何基于“排序 + 截断”的策略都视为**错误**（SQL: `ORDER BY ... LIMIT 1`; pandas: `idxmin/idxmax/head(1)/iloc[0]/nsmallest(1)/nlargest(1)` 等）。
    - 你**必须**返回所有满足 `metric == global_optimum` 的行（ties 全部返回）。
    - 即使你预计只有 1 行，也必须走“先算 optimum 再等值过滤”的流程（这条用于强制路径一致性，减少偷懒分支）。
- Canonical 模板（将写入 prompt，作为“必须模板”而不是建议）：
  - **SQL（DuckDB）**：
    - `WITH agg AS (<your GROUP BY query without ORDER BY/LIMIT>) SELECT * FROM agg QUALIFY metric = MIN(metric) OVER ();`
    - 对 max：`QUALIFY metric = MAX(metric) OVER ();`
  - **pandas**：
    - `min_v = df['metric'].min(); out = df[df['metric'] == min_v]`
    - 若仅输出单列：`out[['col']].drop_duplicates()`（禁止 `.iloc[0]`）

#### B2) QA Reviewer Prompt：强制“单行最优解”一律质疑并拒绝（除非有 tie-check 证据）

**文件：** [prompt.py:ANSWER_REVIEW_SYSTEM_PROMPT](file:///workspace/src/data_agent_baseline/agents/prompt.py#L96-L122)

- reviewer 需要的不是“允许多行”，而是**强制验证分支**：
  - 对 min/max 类问题：
    - 若答案只有 1 行：reviewer **必须**默认认为存在漏召回风险并 REJECT，要求 agent 提供 tie-check（证明其使用等值过滤而非排序截断）。
    - 若答案多行：reviewer 检查列/语义是否对齐即可。
  - 为避免 unique optimum 的死循环，计划同时引入“tie-check 证据机制”（见 C1/C2）。

### C. 工具层强制路径：把 tie-check 变成“可验证的提交协议”

#### C1) `answer` 工具引入 `tie_checked` 协议字段（半硬约束）

**文件：** [registry.py:_answer](file:///workspace/src/data_agent_baseline/tools/registry.py#L142-L206) 与 [prompt.py](file:///workspace/src/data_agent_baseline/agents/prompt.py)

- 背景：仅靠列举禁止项永远不完备（`ORDER BY ... LIMIT 1` / `nsmallest(1)` / 复杂 sort+drop_duplicates+head 等会不断变体）。需要让工具层能验证“是否走过等值过滤路径”。
- 方案：扩展 `answer` action_input 支持一个额外字段（不影响评测输出）：
  - `tie_checked: true`
- 约束（半硬）：
  - 若问题被判定为 min/max 类最优化题：
    - `len(rows) == 1` 且未提供 `tie_checked=true` → **直接返回错误**（强制 retry）
    - `len(rows) >= 2` → 接受（ties 已体现）
    - `len(rows) == 1` 且提供 `tie_checked=true` → 接受（代表 agent 已按协议做过等值过滤验证）
- prompt 同步要求：min/max 类任务最终 `answer` 必须带上 `tie_checked=true`（除非答案本身多行）。

#### C2) “min/max 题型识别”抽象为规则，而不是黑名单枚举

**文件：** [registry.py:_answer](file:///workspace/src/data_agent_baseline/tools/registry.py#L142-L206)

- 题型识别基于 question 关键词（min/max/lowest/highest/best/worst/cheapest/most expensive/earliest/latest 等）与排除词（纯聚合：sum/count/average/ratio 等）。
- 工具层不去枚举所有违规 API，而是用 `tie_checked` 协议把“是否验证过 cardinality”变为可检查信号，从而彻底覆盖排序截断变体。

## Assumptions & Decisions

- 决策：对“最小/最大类最优化选择题”一律采用 set-based 默认语义（返回全部并列最优），这与用户观察到的 DataAgent-Bench gold 行为对齐。
- 决策：DuckDB 修复优先于 pandas workaround；schema/SQL 能用时尽量用 DuckDB 直接聚合/筛选，减少 pandas 手写逻辑分叉。
- 决策：把“tie-check 是否执行”从隐含推断改成显式协议（`tie_checked=true`），并在工具层强制执行，避免 reviewer/模型偷懒放行。

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

### 2b) 协议回归：min/max 单行答案必须显式 `tie_checked=true`

- 人工/日志检查：
  - 对 min/max 类任务，如果最终 prediction 仅 1 行，trace 中 `answer` action_input 必须包含 `tie_checked=true`。
  - 如果 agent 试图用 `LIMIT 1 / idxmin / iloc[0]` 直接提交单行且未声明 tie_checked，应被工具层拒绝并触发 reflection 重试。

### 3) 评测侧校验（可选）

- 对新的 run 目录执行：
  - [kdd_eval.py](file:///workspace/src/data_agent_baseline/benchmark/kdd_eval.py) 的 compare/evaluate 流程，确认行数不匹配类错误显著下降。
