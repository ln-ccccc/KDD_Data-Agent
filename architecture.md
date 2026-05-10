# KDD Cup 2026 Data Agent — 技术规格 v2.0

> 架构：Hybrid Plan-and-Execute + ReAct，LangGraph 编排
> 评估模型：Qwen3.5-35B-A3B（vLLM，context 262K tokens，seed 1024，无 GPU）
> 官方工具集：8 个（签名不可改）
> 提交形式：Docker image，linux/amd64

---

## 目录

1. [设计哲学与模式选型](#1-设计哲学与模式选型)
2. [整体架构与模块职责](#2-整体架构与模块职责)
3. [Graph 节点与边定义（LangGraph）](#3-graph-节点与边定义langgraph)
4. [Tool 完整规格](#4-tool-完整规格)
5. [State 与 Memory 规格](#5-state-与-memory-规格)
6. [Plan-and-Execute 规格](#6-plan-and-execute-规格)
7. [ReAct Loop 规格](#7-react-loop-规格)
8. [Reflection 与 Feedback Engine](#8-reflection-与-feedback-engine)
9. [AnswerReview 规格](#9-answerreview-规格)
10. [Context Policy 与 Memory 规格](#10-context-policy-与-memory-规格)
11. [Prompt 规格](#11-prompt-规格)
12. [并行、超时与 SIGTERM 处理](#12-并行超时与-sigterm-处理)
13. [输出规范](#13-输出规范)
14. [开发规范](#14-开发规范)
15. [文件结构](#15-文件结构)
16. [测试规范](#16-测试规范)

---

## 1. 设计哲学与模式选型

### 1.1 为什么是 Hybrid，不是纯 ReAct 或纯 Plan-and-Execute

**纯 ReAct 的问题（官方 baseline 的弱点）：**
- 没有全局视野，模型在每一步只看到局部 observation，容易陷入局部最优（反复执行相同 SQL，只改变细节）
- step 消耗无计划，easy 任务浪费 10 步，hard 任务在第 12 步才意识到还没读文档
- 无法处理"先查 A 表，用结果筛 B 表"的多阶段依赖

**纯 Plan-and-Execute 的问题：**
- Plan 生成于任务开始，没有执行数据。对于 medium/hard 任务，第一步 `inspect_schema` 的结果会让 Plan 完全作废
- "Plan 失败"没有优雅的处理路径——重新 Plan 浪费步骤，强行执行错误 Plan 得到错误答案
- 对 extreme 任务，Plan 本身的 prompt 就可能超出 token 预算

**Hybrid 的设计意图：**

```
Plan（轻量，仅定向）→ ReAct（具体执行）→ Reflection（局部修正）→ AnswerReview（全局校验）
```

Plan 不是详细步骤，是**执行方向**（用哪些工具，读哪些文件，走哪条 pipeline）。
ReAct 在方向内自由决策每一步的具体内容。
Reflection 在执行失败时修正单步，不重新 Plan。
AnswerReview 在提交前校验全局，必要时触发单次重试。

**Plan 的可修正性**（核心设计）：

Plan 支持一次"部分修订"（Replan），触发条件是 Reflection 检测到 Plan 的方向性错误（不是执行错误）：
- 预期读 CSV，但实际数据在 SQLite 中
- 预期单表查询，实际需要跨 3 张表 join
- 预期文档长度可直接读，实际 token 超过 60K

Replan 只修改剩余步骤，已完成的观察（schema、knowledge.md 内容）保留在 Memory 中复用。

### 1.2 三条不可违背的原则

| 原则 | 含义 |
|---|---|
| **执行结果是唯一可信信号** | LLM 的自我评估不可靠。Reflection 和 AnswerReview 的输入必须包含实际执行结果，不接受 LLM 的"我认为结果是正确的" |
| **精准 > 完整** | 评分公式 `Score = Recall − λ·(Extra/Predicted)` 惩罚多余列。AnswerReview 专门检查列精准度 |
| **时间感知贯穿全程** | 12h 总时限、每任务软时限、step budget 三层控制，每个节点进入前检查剩余预算 |

---

## 2. 整体架构与模块职责

```
solution/
├── core/              # 基础层：类型定义、异常、常量
├── runtime/           # 运行时内核：State、Memory、Policy、Reducer
├── agents/            # 代理逻辑：Model、ReAct、PlanExecute、Prompt
├── tools/             # 工具集：官方8个 + RAG + DuckDB + SubAgent
├── orchestrators/     # LangGraph 编排：Graph 定义、节点、条件边
├── benchmark/         # 评测层：数据集加载、Schema、评分逻辑
└── run/               # 执行入口：Runner、并行控制、产物持久化
```

### 2.1 各模块职责

**core/**
- `types.py`：`RetryPolicy`、`TraceLevel`、`PipelineType`、`ViolationLevel` 等跨模块类型
- `errors.py`：`ToolExecutionError`、`PlanFailedError`、`ContextOverflowError`、`TimeoutError` 统一异常层级
- 约束：不依赖任何其他业务模块

**runtime/**
- `state.py`：`RuntimeState`——LangGraph 的单源事实（SSOT），详见第 5 节
- `memory.py`：`AbstractMemory`——事实列表、资产清单、待办事项，跨 ReAct 步骤持久化
- `policy.py`：`ContextPolicy`——上下文长度监控，触发压缩/RAG/记忆提取的决策中心
- `reducer.py`：将 LLM 原始输出和工具返回值还原为结构化状态更新
- `middlewares/context_compression.py`：滚动截断 + 事实摘要，保留关键 observation

**agents/**
- `model.py`：OpenAI-compatible 客户端封装，读环境变量，统一 latency 记录
- `react.py`：Thought→Action→Observation 单步逻辑
- `plan_execute.py`：Plan 生成、Replan 触发、step budget 分配
- `prompt.py`：所有 System Prompt 和任务 Prompt 模板的集中管理

**tools/**
- `filesystem.py`：`list_context`、`read_csv`、`read_json`、`read_doc`（官方，加 wrapper）
- `sqlite.py`：`inspect_sqlite_schema`、`execute_context_sql`（官方，加 wrapper）
- `python_exec.py`：`execute_python`（官方，加 wrapper）
- `registry.py`：`answer` tool + 工具 schema 注册（官方，最小改动）
- `duckdb_exec.py`：跨文件 SQL（CSV + SQLite 联合查询），非官方，评估时可用
- `knowledge_retriever.py`：BM25 文档检索，纯 CPU，无需 embedding 模型
- `schema_retriever.py`：大 DB 的 schema 裁剪，只注入相关表
- `subagent.py`：子任务委托，extreme 任务的分段推理

**orchestrators/**
- `graph.py`：LangGraph StateGraph 定义，节点注册，条件边配置
- `nodes/llm_node.py`：调用 LLM，更新 messages
- `nodes/tool_node.py`：执行 tool call，收集 observation
- `nodes/reflection_node.py`：Reflection 逻辑，生成 constraint
- `nodes/answer_review_node.py`：答案语义校验，列精准度检查
- `nodes/plan_node.py`：Plan 生成与 Replan

---

## 3. Graph 节点与边定义（LangGraph）

### 3.1 节点清单

```python
from langgraph.graph import StateGraph, END
from runtime.state import RuntimeState

builder = StateGraph(RuntimeState)

builder.add_node("entry",         node_entry)          # 初始化，detect_context
builder.add_node("plan",          node_plan)           # 生成/修订 Plan
builder.add_node("llm",           node_llm)            # LLM 推理，生成 Action
builder.add_node("tool",          node_tool)           # 执行 tool call
builder.add_node("reflection",    node_reflection)     # 失败分析，生成 constraint
builder.add_node("answer_review", node_answer_review)  # 答案语义校验
builder.add_node("replan",        node_replan)         # 修订剩余 Plan
builder.add_node("answer",        node_answer)         # 写 prediction.csv
builder.add_node("timeout",       node_timeout)        # 超时兜底
```

### 3.2 边与条件边

```python
# 固定边
builder.add_edge("entry", "plan")

# plan → llm（正常启动 ReAct）
builder.add_edge("plan", "llm")

# llm → tool（有 tool call）或 answer_review（调用 answer tool）
builder.add_conditional_edges("llm", route_after_llm, {
    "tool":          "tool",
    "answer_review": "answer_review",
    "timeout":       "timeout",      # steps 耗尽
})

# tool → reflection（执行失败）或 llm（执行成功，继续循环）
builder.add_conditional_edges("tool", route_after_tool, {
    "reflection": "reflection",
    "llm":        "llm",
})

# reflection → replan（方向性错误）或 llm（局部修正后继续）
builder.add_conditional_edges("reflection", route_after_reflection, {
    "replan": "replan",
    "llm":    "llm",
})

# replan → llm（修订后继续执行）
builder.add_edge("replan", "llm")

# answer_review → answer（通过）或 llm（未通过，追加修正指令）
builder.add_conditional_edges("answer_review", route_after_review, {
    "answer": "answer",
    "llm":    "llm",    # 最多触发 1 次，之后强制 answer
})

# 终止边
builder.add_edge("answer",  END)
builder.add_edge("timeout", END)

builder.set_entry_point("entry")
graph = builder.compile()
```

### 3.3 路由函数规格

```python
def route_after_llm(state: RuntimeState) -> str:
    if state.steps_remaining <= 0:
        return "timeout"
    if state.last_action_type == "answer":
        return "answer_review"
    return "tool"

def route_after_tool(state: RuntimeState) -> str:
    if state.last_execute_result.is_error:
        return "reflection"
    if state.last_execute_result.has_hard_violation:
        return "reflection"
    return "llm"

def route_after_reflection(state: RuntimeState) -> str:
    # 方向性错误：Plan 本身有问题，需要 Replan
    if state.reflection_result.requires_replan:
        return "replan"
    # 局部错误：注入 constraint 后回到 LLM
    return "llm"

def route_after_review(state: RuntimeState) -> str:
    if state.review_result.passed:
        return "answer"
    if state.review_retry_count >= 1:
        return "answer"   # 强制通过，避免无限循环
    return "llm"
```

### 3.4 Step Budget 分配

step budget 在 `plan` 节点设置，写入 state，每个节点进入时递减检查。

| Difficulty | Budget | 各阶段分配（软性指导） |
|---|---|---|
| easy | 6 | explore(2) + execute(2) + review(1) + answer(1) |
| medium | 10 | explore(2) + plan(1) + execute(4) + reflection(1) + review(1) + answer(1) |
| hard | 14 | explore(2) + plan(1) + retrieve(3) + execute(4) + reflection(2) + review(1) + answer(1) |
| extreme | 16 | retrieve(4) + plan(1) + execute(5) + reflection(3) + review(1) + answer(2) |

budget 写入 System Prompt 的 `[STEP BUDGET]` 段，让 LLM 知道自己的剩余步骤。硬上限仍是官方的 `max_steps=16`。

---

## 4. Tool 完整规格

官方 8 个 tool 签名不可改。Wrapper 层在 `tools/` 模块内部实现，对 LangGraph 的 tool_node 透明。

### 4.1 `list_context`

```python
# 签名（官方，不可改）
def list_context(max_depth: int = 2) -> str: ...

# 调用规范
# - 每个 task 第 1 步必调，max_depth=2
# - 返回值由 Reducer 解析，填充 state.context_info

# Wrapper 增量
# - 解析文件树 → ContextInfo（has_db, has_doc, doc_files, ...）
# - 估算 doc_tokens（sum(file_size) / 3.5）
# - 写入 state.memory.assets（资产清单）
```

### 4.2 `read_csv`

```python
def read_csv(path: str, max_rows: int = 5) -> str: ...

# 调用规范
# - schema 感知：max_rows=3，全量感知：max_rows=10
# - 不用于读取全量数据（用 execute_python 或 duckdb_exec）
# - path 相对于 context/

# Wrapper 增量
# - 解析列名和推断类型 → state.schema_cache[path]
# - 若列数 > 20，记录 WIDE_TABLE soft signal
```

### 4.3 `read_json`

```python
def read_json(path: str, max_chars: int = 2000) -> str: ...

# 调用规范
# - max_chars=1000 用于结构感知
# - 嵌套 JSON 在 execute_python 中展开

# Wrapper 增量
# - 检测顶层是 list 还是 dict，写入 schema_cache
```

### 4.4 `read_doc`

```python
def read_doc(path: str, max_chars: int = 4000) -> str: ...

# 调用规范
# - knowledge.md：max_chars=8000（通常可全量读）
# - doc/ 文件：第一次 max_chars=1500（判断结构），
#   后续由 knowledge_retriever 决定是否继续
# - 无 offset 参数，长文档用 execute_python + Python 分块

# Wrapper 增量
# - 追踪 doc 文件的已读 chars，写入 state.memory.doc_cursors
# - 若 doc_tokens_est > 60K，自动触发 BM25 检索策略
```

### 4.5 `inspect_sqlite_schema`

```python
def inspect_sqlite_schema(path: str) -> str: ...

# 调用规范
# - 每个 db 文件只调用 1 次
# - 返回内容直接注入 SQL prompt 的 [SCHEMA] 段

# Wrapper 增量
# - 解析 DDL → state.schema_cache["tables"]
# - 推断外键（列名模式匹配：*_id → 对应表）
# - 标注大表（行数 > 10K）
# - 若表数 > 10，触发 schema_retriever（只注入相关表）
```

### 4.6 `execute_context_sql`

```python
def execute_context_sql(path: str, sql: str, limit: int = 100) -> str: ...

# 调用规范
# - 验证阶段：limit=10（快速校验结构）
# - 最终答案：limit=1000（确保完整输出）
# - 只读：不支持写操作
# - 错误信息以 "Error:" 开头

# Wrapper → FeedbackEngine 输入
# - 解析 row_count / column_names / null_ratio
# - 检测 "Error:" → SQL_ERROR violation
# - 联合 sqlglot.parse_one(sql) 做 AST 分析

# 返回值结构（解析后）
@dataclass
class SQLExecuteResult:
    raw:          str
    is_error:     bool
    error_msg:    str | None
    row_count:    int
    col_count:    int
    col_names:    list[str]
    null_ratio:   float
    sql_ast:      Any | None   # sqlglot 解析结果
```

### 4.7 `execute_python`

```python
def execute_python(code: str) -> str: ...

# 执行环境
# - cwd = task context/
# - 可用：pandas, numpy, sqlite3, json, re, pathlib
# - BM25：rank_bm25（需打入 Docker）
# - 无网络，无 GPU

# 调用规范（必须遵守的代码模板）
PYTHON_CODE_TEMPLATE = """
import pandas as pd
import sqlite3
# ... 业务逻辑 ...
result = ...  # pd.DataFrame，只含题目要求的列
# ↓ 必须保留，不得删除
print(result.to_csv(index=False))
"""

# Wrapper 增量
# - 检测 observation 是否为空字符串 → MISSING_PRINT violation
# - 检测 "Traceback" / "Error:" → CODE_ERROR violation
# - 解析 print 输出 → PythonExecuteResult

@dataclass
class PythonExecuteResult:
    raw:         str
    is_error:    bool
    error_msg:   str | None
    traceback:   str | None
    row_count:   int
    col_names:   list[str]
    null_ratio:  float
```

### 4.8 `answer`

```python
def answer(columns: list[str], rows: list[list]) -> str: ...

# 调用规范
# - 每个 task 只调用 1 次，调用后 state.done = True
# - 调用前必须已通过 AnswerReview
# - columns 和 rows 每行长度必须一致

# 格式要求（由 format_output() 预处理后传入）
# - float：round(v, 2)
# - datetime：strftime("%Y-%m-%d")
# - null：""（空字符串）
# - string：strip()，移除 \r\n

# Wrapper 增量
# - 自动调用 format_output()
# - 记录 answer_columns / answer_row_count 到 trace
```

### 4.9 扩展 Tool（非官方，评估环境可用）

**`duckdb_exec`**

跨文件 SQL，可同时查询 CSV 和 SQLite：

```python
def duckdb_exec(sql: str, context_dir: str) -> str:
    """
    用 DuckDB 执行 SQL，可直接读取 context/ 下的 CSV 文件。
    
    示例 SQL:
        SELECT a.*, b.revenue
        FROM 'orders.csv' a
        JOIN 'db/sales.db'::orders b ON a.id = b.order_id
        WHERE ...
    
    适用场景：medium 任务中 CSV 和 SQLite 数据需要 join
    """
```

**`knowledge_retriever`**

BM25 文档检索，纯 CPU：

```python
def knowledge_retriever(query: str, doc_paths: list[str], top_k: int = 5) -> list[str]:
    """
    对 doc/ 下的文档进行 BM25 检索，返回最相关的 top_k 段落。
    
    chunk_size: 500 words（约 700 tokens）
    overlap:    100 words
    适用场景：hard/extreme 任务，doc_tokens > 10K
    """
```

**`schema_retriever`**

大 DB 的相关表裁剪：

```python
def schema_retriever(question: str, all_tables: dict, top_k: int = 5) -> dict:
    """
    从所有表的 DDL 中，按问题相关性选出 top_k 张表注入 prompt。
    用 BM25 对列名 + 表名做匹配（不需要 embedding）。
    适用场景：DB 包含 10+ 张表时
    """
```

---

## 5. State 与 Memory 规格

### 5.1 RuntimeState（LangGraph SSOT）

```python
from typing import Annotated
from langgraph.graph.message import add_messages

class RuntimeState(TypedDict):
    # ── 基础信息 ──────────────────────────────────
    task_id:        str
    difficulty:     Literal["easy", "medium", "hard", "extreme"]
    question:       str
    pipeline_type:  Literal["code", "sql", "doc"]

    # ── 对话历史（LangGraph managed） ─────────────
    messages:       Annotated[list, add_messages]

    # ── Plan ──────────────────────────────────────
    plan:           Plan | None               # 当前 Plan
    plan_version:   int                       # Replan 次数
    step_budget:    int                       # 剩余可用步骤
    steps_remaining: int

    # ── Context 信息 ──────────────────────────────
    context_info:   ContextInfo
    schema_cache:   dict[str, Any]            # path → 列名/类型
    memory:         AbstractMemory

    # ── 执行状态 ──────────────────────────────────
    last_action_type:    str | None           # "tool_call" | "answer"
    last_tool_name:      str | None
    last_execute_result: ExecuteResult | None

    # ── Reflection / Review ───────────────────────
    reflection_result:   ReflectionResult | None
    review_result:       ReviewResult | None
    review_retry_count:  int

    # ── 修正追踪 ──────────────────────────────────
    violations:          list[Violation]
    correction_count:    int
    max_corrections:     int                  # 由 difficulty 决定

    # ── 终止 ──────────────────────────────────────
    done:                bool
    answer_columns:      list[str]
    answer_rows:         list[list]

    # ── Trace ─────────────────────────────────────
    trace:               list[TraceEntry]
    start_time:          float
```

### 5.2 AbstractMemory

Memory 跨 ReAct 步骤持久化，与 messages 不同——messages 会被截断，Memory 内容不截断（但做了摘要）。

```python
@dataclass
class AbstractMemory:
    # 从执行历史中提取的事实
    # 例："orders 表有 12,430 行，date 字段格式为 YYYY-MM-DD"
    facts: list[str] = field(default_factory=list)

    # 已发现的数据资产
    # 例：{"db/sales.db": {"tables": ["orders", "products"]}}
    assets: dict[str, Any] = field(default_factory=dict)

    # 待办事项（Plan 执行状态）
    todos: list[TodoItem] = field(default_factory=list)

    # 文档读取游标（用于长文档分块）
    doc_cursors: dict[str, int] = field(default_factory=dict)

    # 已尝试的 SQL/Code（防止重复生成相同内容）
    tried_sqls: list[str] = field(default_factory=list)
    tried_codes: list[str] = field(default_factory=list)

@dataclass
class TodoItem:
    description: str
    status: Literal["pending", "done", "failed", "skipped"]
    depends_on: list[int] = field(default_factory=list)  # 依赖的 todo 索引
```

Memory 在 Reducer 中更新，在 Policy 决策时读取，在 Reflection 节点读取已尝试的 SQL/Code 以生成差异化的 constraint。

### 5.3 Plan 定义

```python
@dataclass
class Plan:
    pipeline_type: Literal["code", "sql", "doc"]
    steps: list[PlanStep]
    rationale: str          # LLM 生成 Plan 时的推理，用于 Replan 参考

@dataclass
class PlanStep:
    index:       int
    description: str         # 给 LLM 看的自然语言步骤描述
    tool_hint:   str         # 建议使用的 tool，非强制
    status:      Literal["pending", "active", "done", "failed"]
    step_limit:  int         # 该步骤的 step 预算（软性）
```

---

## 6. Plan-and-Execute 规格

### 6.1 Plan 节点（`node_plan`）

Plan 节点在 `entry` 之后执行，基于 `context_info` 和 `question` 生成一个轻量的执行方向。

**Plan Prompt 设计原则：**
- Plan 不要求精确步骤，只要求方向和工具选择
- 每个 PlanStep 的 `description` 用自然语言，不要求格式化
- 总 step 数 ≤ step_budget / 2（为 ReAct 自由空间留余量）

```
[PLAN PROMPT]
你正在处理一个数据分析任务。

问题：{question}
可用数据源：{context_info 摘要}
总步骤预算：{step_budget}

请生成一个高层执行方向（不超过 {step_budget//2} 个步骤）：
- 第一步通常是了解数据结构（list_context 已完成，跳过）
- 如有 SQLite，下一步是 inspect_sqlite_schema
- 如有大文档，需要先检索再分析
- 最后一步总是 answer

以 JSON 格式输出：
{
  "pipeline_type": "code|sql|doc",
  "rationale": "...",
  "steps": [
    {"index": 1, "description": "...", "tool_hint": "...", "step_limit": N},
    ...
  ]
}
```

### 6.2 Plan 失败与 Replan 触发

**触发 Replan 的条件**（在 Reflection 节点判断）：

| 条件 | 说明 |
|---|---|
| `SCHEMA_MISMATCH` | inspect_schema 发现 Plan 预期的表不存在 |
| `PIPELINE_WRONG` | Plan 选择了 sql pipeline，但实际答案在 doc 中 |
| `SCOPE_UNDERESTIMATED` | Plan 预计 2 步完成，但已用 6 步仍未执行 |
| `DOC_OVERFLOW` | Plan 未预料到文档超长，需要切换检索策略 |

**不触发 Replan 的条件**（局部修正，在 Reflection 节点处理）：

| 条件 | 处理方式 |
|---|---|
| SQL 语法错误 | 注入 constraint，LLM 修正 SQL |
| 空结果 | 注入 EMPTY_RESULT constraint |
| 代码 Traceback | 注入 CODE_ERROR constraint |
| null_ratio 过高 | 注入 soft signal，提示检查 JOIN |

**Replan 节点（`node_replan`）：**

```python
def node_replan(state: RuntimeState) -> dict:
    """
    只修订剩余的 pending 步骤，不重置已完成的观察。
    已提取的 facts 和 assets 保留在 memory 中复用。
    """
    completed = [s for s in state.plan.steps if s.status == "done"]
    pending   = [s for s in state.plan.steps if s.status == "pending"]

    # 基于 reflection_result.replan_reason 修订 pending 步骤
    new_pending = llm_replan(
        question         = state.question,
        completed_steps  = completed,
        failed_step      = state.reflection_result.failed_step,
        replan_reason    = state.reflection_result.replan_reason,
        memory_facts     = state.memory.facts,
        steps_remaining  = state.steps_remaining,
    )

    return {
        "plan": Plan(
            pipeline_type = state.plan.pipeline_type,
            steps         = completed + new_pending,
            rationale     = state.plan.rationale + f" [replan v{state.plan_version+1}]",
        ),
        "plan_version": state.plan_version + 1,
    }

# Replan 只允许 1 次（plan_version <= 1），防止无限重规划
```

### 6.3 Replan 上限

```python
MAX_REPLAN = 1   # 全局，每个 task 只允许 Replan 1 次

# 超过上限时，Reflection 即使检测到方向性错误，也只做局部修正
# 原因：Replan 本身消耗 1-2 步，超时风险优先于质量追求
```

---

## 7. ReAct Loop 规格

### 7.1 LLM 节点（`node_llm`）

```python
def node_llm(state: RuntimeState) -> dict:
    # 1. 组装 messages（含 system prompt + task prompt + 对话历史）
    # 2. 调用 ContextPolicy 决定是否压缩
    # 3. 调用 LLM（tool_choice="auto"，tools=官方8个schema）
    # 4. 解析 response：tool_call 或 text（finish_reason）
    # 5. 更新 state.messages，递减 steps_remaining
```

**关键参数：**
```python
llm_params = {
    "temperature": 0.0,        # 评估环境 seed=1024，保持确定性
    "max_tokens":  4096,       # 单次生成上限
    "tool_choice": "auto",     # 让模型决定是否调用 tool
}
```

### 7.2 Tool 节点（`node_tool`）

```python
def node_tool(state: RuntimeState) -> dict:
    tool_call = state.messages[-1].tool_calls[0]
    tool_name = tool_call.function.name
    tool_args = json.loads(tool_call.function.arguments)

    try:
        raw_result = tool_registry.execute(tool_name, tool_args)
    except Exception as e:
        raw_result = f"Error: {e}"

    # Wrapper 层解析（非 LLM 调用，纯规则）
    execute_result = parse_execute_result(tool_name, raw_result)

    # 更新 memory（资产、事实、游标）
    new_memory = update_memory(state.memory, tool_name, execute_result)

    return {
        "messages":          [ToolMessage(content=raw_result, ...)],
        "last_tool_name":    tool_name,
        "last_execute_result": execute_result,
        "memory":            new_memory,
    }
```

### 7.3 步骤消耗检查

每次进入 `node_llm` 前，`route_after_llm` 检查 `steps_remaining`：

```python
# steps_remaining 在 node_llm 开始时递减
# 递减后 <= 0 → 路由到 timeout
# 未递减前 == 1 → 这是最后一步，LLM 必须生成 answer tool call
```

当 `steps_remaining == 1` 时，在 System Prompt 注入强制指令：
```
⚠ 这是你的最后 1 步。你必须立即调用 answer tool 提交当前最佳结果。不要再分析。
```

---

## 8. Reflection 与 Feedback Engine

### 8.1 Reflection 节点职责

Reflection 节点在 `tool` 节点检测到错误或 hard violation 后触发。职责是：

1. 判断是局部错误（constraint 修正）还是方向性错误（触发 Replan）
2. 生成 constraint 文字，注入下一轮 LLM 的 context
3. 检查 `memory.tried_sqls` / `tried_codes`，确保修正后的版本与历史版本不同

### 8.2 Violation 完整定义

```python
class ViolationLevel(str, Enum):
    HARD = "hard"    # 必须修正，注入 constraint
    SOFT = "soft"    # 仅记录，写入 trace，不注入

@dataclass
class Violation:
    code:        str
    level:       ViolationLevel
    message:     str
    constraint:  str   # HARD 专属，注入 prompt 的文字
    triggers_replan: bool = False  # 是否触发 Replan
```

### 8.3 Hard Violations 完整列表

| Code | 触发条件 | Constraint 文字 | 触发 Replan |
|---|---|---|---|
| `SQL_ERROR` | execute_context_sql 返回 "Error:" | MUST fix the SQL syntax error: `{error_msg}` | No |
| `CODE_ERROR` | execute_python 含 Traceback | MUST fix the Python error: `{traceback 最后 3 行}` | No |
| `MISSING_PRINT` | execute_python observation 为空字符串 | MUST end the code with: `print(result.to_csv(index=False))` | No |
| `EMPTY_RESULT` | row_count=0 且无否定语义 | MUST NOT return empty result. Relax WHERE or use LEFT JOIN | No |
| `LIMIT_NO_ORDER` | SQL 有 LIMIT 无 ORDER BY | MUST add ORDER BY before LIMIT. Choose the most relevant sort column | No |
| `BAD_AGGREGATE` | has_aggregate + GROUP BY=0 + SELECT 多列 | MUST add GROUP BY for all non-aggregate SELECT columns | No |
| `GROUP_SELECT_MISMATCH` | GROUP BY 字段与 SELECT 非聚合字段不一致 | MUST align GROUP BY with non-aggregate SELECT columns | No |
| `NULL_OVERFLOW` | null_ratio > 0.7 | MUST investigate NULL source. Check JOIN conditions or use COALESCE | No |
| `SINGLE_ROW_SUSPICION` | row_count=1 且 question 含 each/all/list | MUST verify: question implies multiple rows | No |
| `SCHEMA_MISMATCH` | SQL 引用的表/列不在 schema_cache | MUST use only existing tables/columns: `{available}` | **Yes** |
| `PIPELINE_WRONG` | SQL 无结果但 doc 中有相关内容 | 触发 Replan，切换 pipeline | **Yes** |
| `DOC_OVERFLOW` | doc_tokens > 100K 且未启用 BM25 | 触发 Replan，启用 knowledge_retriever | **Yes** |

### 8.4 否定语义检测（EMPTY_RESULT 豁免）

```python
NEGATION_PATTERNS = [
    r"没有|不存在|零个|无(?:任何)?",       # 中文
    r"\bno\b|\bnone\b|\bzero\b",           # 英文
    r"not exist|never|without any",
]

def has_negation_semantics(question: str) -> bool:
    q = question.lower()
    return any(re.search(p, q) for p in NEGATION_PATTERNS)
```

### 8.5 修正次数上限

```python
MAX_CORRECTIONS = {"easy": 1, "medium": 2, "hard": 3, "extreme": 3}

# 达到上限且仍有 HARD violations：
# - 记录到 trace（correction_limit_reached=True）
# - 直接路由到 answer_review，提交当前最佳结果
# - 不再触发 reflection
```

### 8.6 AST 变化检测（防止无效修正）

```python
import sqlglot

def ast_changed(sql_a: str, sql_b: str) -> bool:
    try:
        return sqlglot.parse_one(sql_a) != sqlglot.parse_one(sql_b)
    except Exception:
        return sql_a.strip() != sql_b.strip()

# 若修正后的 SQL 与上一版本 AST 相同：
# - 触发 IDENTICAL_SQL soft signal
# - 在 constraint 中补充：
#   "MUST make a structurally different SQL (not just rename aliases)"
# - 同时注入 memory.tried_sqls 中的历史版本，防止模型重复
```

### 8.7 Reflection 节点 Prompt

```
[REFLECTION PROMPT]
上一步执行失败或结果异常。

工具：{tool_name}
输入：{tool_input_summary}
输出：{observation_summary}

检测到的问题：
{violation_list}

已尝试过的方案（不要重复）：
{memory.tried_sqls 或 tried_codes 的摘要}

请分析根本原因，然后：
1. 如果是局部执行错误（SQL 语法、Python 错误、空结果），输出修正后的工具调用
2. 如果是方向性错误（表不存在、pipeline 选错、文档远超预期），输出 {"requires_replan": true, "reason": "..."}

约束：
{constraint_list}   ← 每条以 MUST / MUST NOT 开头
```

---

## 9. AnswerReview 规格

### 9.1 节点职责

AnswerReview 在 `answer` tool 被调用前触发，对候选答案做语义校验。是**最后一道防线**，主要检查：

1. 列数是否超过题目要求（Extra Column 惩罚预防）
2. 数值精度是否符合规范（2 位小数）
3. 结果是否与题目的筛选/排序/聚合要求一致
4. 日期格式是否为 YYYY-MM-DD

### 9.2 ReviewResult 定义

```python
@dataclass
class ReviewResult:
    passed:         bool
    issues:         list[str]        # 发现的问题
    fix_instruction: str | None      # 注入 LLM 的修正指令（仅 passed=False 时）

    # 列分析
    required_cols:  int              # 估算题目要求的列数
    predicted_cols: int              # 候选答案的列数
    extra_cols:     list[str]        # 多余的列名
```

### 9.3 列数估算

```python
COLUMN_PATTERNS = [
    (r"list (?:the )?(\w+) and (?:the )?(\w+) and",  3),
    (r"list (?:the )?(\w+) and (?:the )?(\w+)",       2),
    (r"\beach\b|\ball\b|\bevery\b",                    2),
    (r"\btop \d+\b",                                   2),
    (r"sort|rank|order by",                             2),
    (r"total|sum|average|count",                        2),
]

def estimate_required_columns(question: str) -> int:
    q = question.lower()
    for pattern, count in COLUMN_PATTERNS:
        if re.search(pattern, q):
            return count
    return 2   # 默认

def check_extra_columns(candidate_cols: list[str], question: str) -> list[str]:
    required = estimate_required_columns(question)
    if len(candidate_cols) > required + 1:
        return candidate_cols[required + 1:]
    return []
```

### 9.4 AnswerReview 触发策略

```python
# AnswerReview 总是触发（每个 task 都做）
# 若发现问题，注入修正指令并路由回 llm（最多 1 次）
# 第二次 AnswerReview 必定 passed=True（强制通过，避免循环）

def route_after_review(state: RuntimeState) -> str:
    if state.review_result.passed:
        return "answer"
    if state.review_retry_count >= 1:
        # 强制写盘，宁可得部分分也不超时
        return "answer"
    return "llm"
```

---

## 10. Context Policy 与 Memory 规格

### 10.1 ContextPolicy

LLM 上下文上限 262K tokens，长对话历史会耗尽预算。Policy 是上下文长度的主动管理器。

```python
CONTEXT_LIMITS = {
    "max_total":     200_000,   # 触发压缩的阈值（留 60K 给 schema/retrieve）
    "max_history":   80_000,    # 纯对话历史的上限
    "keep_recent":   4,         # 压缩时保留最近 N 轮
}

class ContextPolicy:
    def should_compress(self, state: RuntimeState) -> bool:
        total = estimate_tokens(state.messages)
        return total > CONTEXT_LIMITS["max_total"]

    def compress(self, state: RuntimeState) -> list[dict]:
        """
        保留：
          - System prompt（第 0 条）
          - 最近 keep_recent 轮对话
          - memory.facts 转换的摘要消息
        删除：
          - 中间的 tool result（Observation），尤其是大表的打印输出
        """
        system   = state.messages[:1]
        recent   = state.messages[-self.keep_recent * 2:]
        facts_msg = [{"role": "system", "content":
                      "已知事实（来自历史执行）：\n" + "\n".join(state.memory.facts)}]
        return system + facts_msg + recent

    def should_retrieve(self, state: RuntimeState) -> bool:
        """决定是否触发 BM25 检索（替代直接 read_doc）"""
        return (state.context_info.doc_tokens_est > 8_000
                and state.pipeline_type == "doc")

    def should_extract_facts(self, state: RuntimeState) -> bool:
        """决定是否从当前 observation 中提取事实存入 memory"""
        # 每执行 3 步 extract 一次，或在 schema inspect 后立即 extract
        return (state.steps_remaining % 3 == 0
                or state.last_tool_name == "inspect_sqlite_schema")
```

### 10.2 事实提取（Memory 更新）

事实提取不调用 LLM，用规则从 observation 中提取：

```python
def extract_facts(tool_name: str, result: ExecuteResult) -> list[str]:
    facts = []
    if tool_name == "inspect_sqlite_schema":
        for table in result.tables:
            facts.append(f"表 {table.name} 有 {table.row_count} 行，"
                         f"列：{', '.join(table.columns)}")
    elif tool_name in ("execute_context_sql", "execute_python"):
        if result.row_count > 0:
            facts.append(f"最近查询返回 {result.row_count} 行，"
                         f"列：{', '.join(result.col_names)}")
    return facts
```

---

## 11. Prompt 规格

### 11.1 System Prompt 结构

```
[ROLE]
你是一个专业数据分析 Agent。你通过调用工具来探索数据、执行查询、生成准确的表格答案。

[TOOLS]
{tool_schemas}

[TASK INFO]
难度：{difficulty}
Pipeline 类型：{pipeline_type}
剩余步骤：{steps_remaining} / {step_budget}

[CURRENT PLAN]
{plan.steps 的当前状态（pending/active/done/failed）}

[KNOWN FACTS]
{memory.facts}

[CONSTRAINTS]
- 最终答案通过 answer tool 提交，每个任务只调用 1 次
- execute_python 的代码必须以 print(result.to_csv(index=False)) 结尾
- 列数只输出题目明确要求的列，不多输出
- 数值保留 2 位小数，日期格式 YYYY-MM-DD，null 写为空字符串

[SCORING NOTE]
评分 = Recall − λ × (多余列 / 预测列总数)
多一列无关列 = 扣分。宁少勿多。

{steps_remaining == 1 时注入：}
⚠ 这是最后 1 步。立即调用 answer tool 提交当前最佳结果。
```

### 11.2 Task Prompt 结构（用户消息，task 开始时）

```
[QUESTION]
{question}

[CONTEXT FILES]
{list_context 输出，第 1 步自动注入}

[SCHEMA]（sql pipeline，inspect_schema 后注入）
{schema_cache 中的相关表 DDL}

[KNOWLEDGE]（knowledge.md 存在时注入）
{knowledge.md 全文或 BM25 检索结果}
```

### 11.3 Constraint 注入格式（Reflection 后）

```
⚠ 上一步执行出现问题，请按以下约束修正：

问题：
{violation_list}

必须遵守：
- MUST ...
- MUST NOT ...

不能改变：
- 不增加题目未要求的列
- 修正后的代码/SQL 必须与历史版本有实质差异

历史失败版本（不要重复）：
{memory.tried_sqls[-3:] 或 tried_codes[-3:]}
```

---

## 12. 并行、超时与 SIGTERM 处理

### 12.1 任务分级并行

```python
TIME_BUDGET = {
    "easy":    3 * 60,
    "medium":  8 * 60,
    "hard":    20 * 60,
    "extreme": 40 * 60,
}

GLOBAL_BUDGET_SECONDS = 11 * 3600  # 留 1h 给写盘和日志

def run_all_tasks(tasks: list[Path], cfg: Config):
    easy_med  = [t for t in tasks if get_difficulty(t) in ("easy", "medium")]
    hard_ext  = [t for t in tasks if get_difficulty(t) in ("hard", "extreme")]

    # easy/medium 并行（8 workers）
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futs = {pool.submit(run_single, t, cfg): t for t in easy_med}
        for f in as_completed(futs):
            try:
                save_result(f.result(timeout=TIME_BUDGET["medium"]))
            except TimeoutError:
                save_empty(futs[f])
                log.warning(f"Task {futs[f]} timed out")

    # hard/extreme 串行（避免大文档同时加载 OOM）
    for t in hard_ext:
        if _shutdown_flag.is_set():
            break
        if time.time() - _global_start > GLOBAL_BUDGET_SECONDS:
            save_empty(t)
            continue
        try:
            result = run_with_timeout(run_single, t, cfg,
                                       timeout=TIME_BUDGET.get(get_difficulty(t)))
            save_result(result)
        except TimeoutError:
            save_empty(t)
```

### 12.2 SIGTERM Graceful Shutdown

```python
import signal, threading

_shutdown_flag = threading.Event()
_global_start  = time.time()

def _handle_sigterm(sig, frame):
    log.warning("SIGTERM received, finishing current tasks and exiting...")
    _shutdown_flag.set()

signal.signal(signal.SIGTERM, _handle_sigterm)

# 每个任务开始前检查 shutdown flag
# SIGTERM 后 30s 内 SIGKILL，已写盘的 prediction.csv 仍参与评分
# 原则：宁可部分任务得 0 分，不能让已完成的任务丢失写盘机会
```

### 12.3 每任务写盘策略

```python
def run_single(task_dir: Path, cfg: Config) -> TaskResult:
    result = TaskResult(task_id=task_dir.name, df=pd.DataFrame())
    try:
        df = graph.invoke(initial_state, config=cfg)["answer_df"]
        result.df = df
    except Exception as e:
        log.error(f"Task {task_dir.name} failed: {e}")
    finally:
        # finally 块确保无论如何都写盘
        out = output_dir / task_dir.name / "prediction.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        result.df.to_csv(out, index=False)
        write_trace(task_dir.name, result.trace)
    return result
```

---

## 13. 输出规范

### 13.1 `format_output()`

```python
def format_output(df: pd.DataFrame) -> pd.DataFrame:
    """所有 prediction.csv 在写盘前必须过此函数。"""
    for col in df.columns:
        if pd.api.types.is_float_dtype(df[col]):
            df[col] = df[col].round(2)
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_object_dtype(df[col]):
            df[col] = (df[col].astype(str).str.strip()
                               .str.replace(r'\r\n|\r|\n', ' ', regex=True))

    df = df.fillna("").replace({"None": "", "nan": "", "NaN": "", "NaT": ""})
    df.columns = [c.strip() for c in df.columns]
    return df
```

### 13.2 Trace 格式（`trace.json`）

```json
{
  "task_id": "task_042",
  "difficulty": "medium",
  "pipeline": "sql",
  "plan_version": 0,
  "elapsed_s": 18.4,
  "steps": [
    {
      "step": 1, "node": "tool",
      "tool": "inspect_sqlite_schema",
      "output_summary": "tables: orders(5 cols,12430 rows), products(4 cols)",
      "facts_extracted": ["orders 有 12430 行"],
      "timestamp_s": 1.1
    },
    {
      "step": 3, "node": "tool",
      "tool": "execute_context_sql",
      "sql": "SELECT ...",
      "feedback": {
        "row_count": 0,
        "violations": [{"code": "EMPTY_RESULT", "level": "hard"}],
        "constraint": "MUST NOT return empty..."
      },
      "timestamp_s": 5.2
    }
  ],
  "reflection_count": 1,
  "review_passed": true,
  "final_answer": {
    "columns": ["category", "revenue"],
    "row_count": 4
  }
}
```

---

## 14. 开发规范

### 14.1 分支策略

```
main         ← 仅存放通过完整测试的稳定版本，对应 Docker 提交
dev          ← 日常开发主分支
feature/xxx  ← 功能分支，合入 dev 前通过单测
```

**提交前必须：**
1. `ruff check . && ruff format .`
2. `pytest tests/ -x --tb=short`
3. `uv run dabench run-benchmark --config configs/local.yaml --limit 20`（公开集前 20 题）

### 14.2 代码规范

| 项目 | 规范 |
|---|---|
| 语言 | Python 3.11+ |
| 格式化 | ruff format（项目已配置） |
| 类型注解 | 所有公共函数必须有 |
| Docstring | Google style，模块/类/公共函数必须有 |
| 日志 | `logging.getLogger(__name__)`，不用 print 调试 |
| 全局状态 | 禁止。所有状态在 RuntimeState 中传递 |
| LLM 调用 | 只通过 `agents/model.py` 的 `LLMClient.chat()` |
| FeedbackEngine | 禁止调用 LLM，全部规则化（确定性） |

### 14.3 LLM 调用规范

```python
# agents/model.py — 唯一 LLM 调用入口
class LLMClient:
    def __init__(self):
        self.client = OpenAI(
            base_url=os.environ["MODEL_API_URL"],
            api_key=os.environ.get("MODEL_API_KEY", "EMPTY"),
        )
        self.model = os.environ.get("MODEL_NAME", "qwen3.5-35b-a3b")

    def chat(self, messages, tools=None, temperature=0.0,
             max_tokens=4096, task_id="") -> ChatCompletion:
        messages = ContextPolicy().compress_if_needed(messages)
        start = time.time()
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages,
            tools=tools, temperature=temperature, max_tokens=max_tokens,
        )
        log_llm_call(task_id, time.time() - start, resp.usage)
        return resp
```

### 14.4 错误处理规范

**不允许未捕获异常传播到 task 主循环外**（否则 prediction.csv 不写盘，得 0 分）。

```python
# 所有 tool 调用在 node_tool 中统一 try/except
# node_tool 的返回值包含 is_error 字段，由 route_after_tool 处理
# 绝对不 raise 到 LangGraph 图外

# 唯一允许 raise 的地方：
# - TimeoutError（由外层 runner 捕获，写空 DataFrame）
# - 内存 OOM（操作系统级，无法捕获，依靠 finally 写盘）
```

### 14.5 环境变量

```bash
# .env.local（不进 git，不打入 Docker）
MODEL_API_URL=https://api.openai.com/v1
MODEL_API_KEY=sk-...
MODEL_NAME=gpt-4o-mini    # 本地用便宜模型跑通流程
```

评估时由比赛方注入，代码只读环境变量，永远不硬编码。

---

## 15. 文件结构

```
solution/
├── Dockerfile
├── pyproject.toml
├── configs/
│   ├── react_baseline.example.yaml   # 官方示例（不改）
│   └── local.yaml                    # 本地开发（不进 Docker）
│
├── core/
│   ├── types.py          # RetryPolicy, TraceLevel, PipelineType, ViolationLevel
│   └── errors.py         # 统一异常层级
│
├── runtime/
│   ├── state.py          # RuntimeState（LangGraph TypedDict）
│   ├── memory.py         # AbstractMemory, TodoItem
│   ├── policy.py         # ContextPolicy（压缩/RAG/事实提取触发）
│   ├── reducer.py        # LLM 输出 → 结构化状态更新
│   └── middlewares/
│       └── context_compression.py
│
├── agents/
│   ├── model.py          # LLMClient（唯一 LLM 入口）
│   ├── react.py          # Thought→Action→Observation 单步
│   ├── plan_execute.py   # Plan 生成、Replan 逻辑
│   └── prompt.py         # 所有 Prompt 模板集中管理
│
├── tools/
│   ├── filesystem.py     # list_context, read_csv, read_json, read_doc
│   ├── sqlite.py         # inspect_sqlite_schema, execute_context_sql
│   ├── python_exec.py    # execute_python
│   ├── registry.py       # answer tool + 工具注册
│   ├── duckdb_exec.py    # 跨文件 SQL（CSV + SQLite 联合）
│   ├── knowledge_retriever.py  # BM25 文档检索
│   ├── schema_retriever.py     # 大 DB schema 裁剪
│   └── subagent.py             # 子任务委托
│
├── orchestrators/
│   ├── graph.py          # LangGraph StateGraph 定义
│   └── nodes/
│       ├── plan_node.py
│       ├── llm_node.py
│       ├── tool_node.py
│       ├── reflection_node.py
│       └── answer_review_node.py
│
├── benchmark/
│   ├── dataset.py        # 官方数据集加载
│   ├── schema.py         # Task / AnswerTable 数据结构
│   └── kdd_eval.py       # 评分逻辑（Recall − λ·Extra）
│
├── run/
│   └── runner.py         # 并行执行 + 超时 + SIGTERM 处理
│
└── tests/
    ├── test_feedback_engine.py
    ├── test_router.py
    ├── test_plan.py
    ├── test_answer_review.py
    ├── test_output.py
    └── fixtures/
```

---

## 16. 测试规范

### 16.1 必须覆盖的单元测试

```python
# test_feedback_engine.py
def test_empty_result_triggers_hard(): ...
def test_empty_result_negation_exempt(): ...
def test_limit_without_order(): ...
def test_sql_error(): ...
def test_missing_print_violation(): ...
def test_max_corrections_hard_stop(): ...
def test_ast_change_detection(): ...
def test_schema_mismatch_triggers_replan(): ...

# test_plan.py
def test_plan_generated_from_context(): ...
def test_replan_preserves_completed_steps(): ...
def test_replan_max_once(): ...
def test_plan_step_budget_respected(): ...

# test_answer_review.py
def test_extra_columns_detected(): ...
def test_correct_columns_pass(): ...
def test_review_retry_max_once(): ...
def test_format_output_float_round(): ...
def test_format_output_date_iso(): ...
def test_format_output_null_empty_string(): ...

# test_router.py
def test_db_routes_to_sql(): ...
def test_large_doc_routes_to_doc(): ...
def test_csv_only_routes_to_code(): ...
```

### 16.2 集成测试目标分数（公开集）

| Difficulty | 目标 Recall | 说明 |
|---|---|---|
| easy | ≥ 0.85 | pandas 直接路径，修正 1 次足够 |
| medium | ≥ 0.65 | SQL + Reflection 覆盖常见错误 |
| hard | ≥ 0.45 | BM25 检索质量是瓶颈 |
| extreme | ≥ 0.30 | 滑动窗口信息损失，属预期 |

---

*v2.0 — 合并自原始重构方案（Feedback Engine / 执行反馈驱动）与新 spec（LangGraph / Plan-Execute / Reflection / AnswerReview / Memory）*
SPECEOF
