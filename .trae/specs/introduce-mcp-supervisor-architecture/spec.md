# MCP 化工具生态与 Supervisor 多 Agent 架构升级 Spec

## Why
当前系统的工具描述与 schema 以 Python 数据类直接硬编码，并且一次性把 20+ 工具预加载到提示词中，导致 Token 浪费、上下文易溢出、工具扩展困难。面向 `https://dataagent.top/` 比赛的全自动运行特性，需要在离线/弱网环境下仍可稳定运行，并具备更强的任务分解、调度与自纠错能力。

## What Changes
- 引入 MCP Server/Client 抽象层，将现有内置工具封装为 MCP tools，并支持外部 MCP server 动态注册（含离线缓存与白名单策略）。
- 引入 Supervisor + Specialist Agents 的协作架构：规划器 → 调度器 → 执行器 pipeline，并提供最小可行的“共享黑板/消息总线”接口。
- 将工具按能力分组，并实现按阶段/按需加载的工具注入策略，避免全量工具描述进入 prompt。
- 引入 TTL/LRU 缓存层，覆盖 schema 采样、schema 检索结果、重复 SQL 结果、嵌入向量等可复用结果；提供缓存失效与一致性策略。
- 上下文管理升级：语义压缩（LLM 摘要）、Token budget 动态分配、关键信息优先级保留。
- LLM 调用参数从字符预算升级为 Token 预算（按模型动态计算），并支持 embedding 请求 batch。
- Embedding 模型可配置（含维度与提供方），并保证在离线/弱网时可回退到本地或禁用策略。
- 增强错误风暴检测（模式识别与策略切换）与并发安全（共享资源加锁、每任务独立状态）。
- **可选**：引入持久化记忆（跨任务的“经验/提示”与关键资产索引），默认关闭，仅在确定收益与稳定性后启用。

## Impact
- Affected specs: Tooling, Tool Registry, Orchestration, Context Management, Caching, Concurrency, Testing.
- Affected code:
  - `src/data_agent_baseline/tools/*`（工具定义、注册、动态加载）
  - `src/data_agent_baseline/orchestrators/*`（Supervisor/Graph 编排）
  - `src/data_agent_baseline/runtime/*`（上下文压缩、policy、state、memory、trace）
  - `src/data_agent_baseline/config.py`（token 预算、embedding 配置、缓存配置、MCP 配置）

## ADDED Requirements
### Requirement: MCP 工具标准化与动态注册
The system SHALL provide 一个 MCP Server/Client 层，使工具可以以 MCP tool 形式被统一描述、发现、调用与隔离。

#### Scenario: 内置工具 MCP 化
- **WHEN** 系统启动并加载默认工具集
- **THEN** 内置工具（如 SQL/Python/文件读取/Schema 检索等）以 MCP tools 的统一接口暴露
- **AND THEN** 工具描述与 input schema 不再依赖 prompt 全量注入，而是由 Tool Provider 按需提供

#### Scenario: 外部 MCP server 动态注册
- **WHEN** 配置文件声明外部 MCP server（本地进程/本地 socket/HTTP 端点）
- **THEN** 系统在启动阶段完成注册与健康检查
- **AND THEN** 外部工具可被发现并加入“可用工具集合”

#### Scenario: 离线可用（无外网）
- **WHEN** 运行环境不可访问互联网
- **THEN** 系统只允许使用本地内置工具与已缓存的外部工具定义
- **AND THEN** 任何需要外部网络的注册/更新动作被跳过并记录为可诊断事件（不导致任务失败）

### Requirement: 工具分组与按需加载
The system SHALL provide 工具分组与动态加载能力，以减少 Token 占用并提升决策稳定性。

#### Scenario: 阶段化工具注入
- **WHEN** 进入不同任务阶段（如：探索/定位 schema/编写 SQL/执行与校验/收敛提交）
- **THEN** 仅注入当前阶段必要的工具描述与 schema
- **AND THEN** 未使用的工具在 prompt 中被裁剪或延后

### Requirement: Supervisor + Specialist Agent 协作架构
The system SHALL provide Supervisor 主导的多 Agent pipeline，实现任务分解、调度与执行的职责分离，且每个 Agent 拥有独立状态与可配置工具集。

#### Scenario: Planner → Dispatcher → Executor
- **WHEN** 收到一个复杂任务（需要多步探索与多次执行）
- **THEN** Planner 产出可执行的子任务列表与成功判定标准
- **AND THEN** Dispatcher 将子任务分配给合适的 Specialist Agent（如 Data Analyst / Debugger / Validator）
- **AND THEN** Executor 执行子任务并产出结构化结果回写到共享黑板

#### Scenario: Agent 间通信（共享黑板）
- **WHEN** Specialist Agent 产出中间发现（如关键表、字段映射、可复用 SQL 片段）
- **THEN** 该信息以结构化条目写入共享黑板
- **AND THEN** Supervisor 在后续决策中可检索与引用这些条目

### Requirement: 缓存层（TTL + LRU）与可复用结果
The system SHALL provide 一个可配置的缓存层，用于降低重复计算与重复工具调用成本，并减少 token 与时间开销。

#### Scenario: Schema/采样缓存
- **WHEN** 同一任务周期内多次请求同一数据源的 schema/采样
- **THEN** 系统命中缓存并直接返回结果

#### Scenario: 重复 SQL 结果缓存
- **WHEN** 多次执行完全一致的只读 SQL（同一路径与同一 SQL 文本与同一 limit）
- **THEN** 系统优先返回缓存结果（在 TTL 内）

### Requirement: Token Budget 精确计算与动态裁剪
The system SHALL provide 基于 tokenizer 的 Token 预算管理，按不同模型动态限制输入长度并在超限时进行可解释的裁剪/摘要。

#### Scenario: Token 上限约束
- **WHEN** 构建 prompt 时将超过模型 context window
- **THEN** 系统按优先级丢弃或摘要历史步骤与工具输出
- **AND THEN** 保留 schema 信息与错误信息的优先级高于一般输出

### Requirement: Embedding 与请求合并
The system SHALL provide embedding 请求的 batch 能力，并允许通过配置切换 embedding 模型与参数。

#### Scenario: Batch embedding
- **WHEN** 需要对多段文本进行 embedding（如多表 schema、多个候选字段描述）
- **THEN** 系统合并为单次批请求或尽可能少的请求次数

### Requirement: 错误风暴检测与策略切换
The system SHALL provide 更智能的错误模式识别（不仅是连续相同错误），并在触发阈值时切换策略以避免无效重试。

#### Scenario: 多次重试同一 SQL
- **WHEN** 同一 SQL 或同类 SQL 连续失败达到阈值
- **THEN** 系统触发策略切换（如：回退到 schema 检索、生成更小的探索 query、或委派给 Debugger Specialist）

### Requirement: 并发安全与任务隔离
The system SHALL ensure 每个任务拥有独立 RuntimeState，而共享资源（embedding 模型、缓存、MCP 连接池）具备线程安全保障。

#### Scenario: 并发运行多个任务
- **WHEN** RunConfig.max_workers > 1 并行运行任务
- **THEN** 不同任务的上下文、记忆与步骤记录互不污染
- **AND THEN** 共享资源访问遵循锁或无锁并发策略，避免竞态

## MODIFIED Requirements
### Requirement: 上下文管理（从字符预算升级为 token 预算）
The system SHALL replace `max_prompt_chars` 为 `max_prompt_tokens`（按模型动态计算），并在构建 prompt 时严格遵循 token 预算。

## REMOVED Requirements
### Requirement: 全量工具描述预注入
**Reason**: 预加载所有工具导致 Token 浪费、上下文易溢出且扩展困难。
**Migration**: 将工具定义迁移为 MCP tool provider，并通过阶段化/按需策略向 Agent 暴露最小集合。
