# 比赛高 ROI 稳定性优化 Spec（Token 预算 / 缓存 / 错误策略 / 工具按需加载）

## Why
比赛的核心收益通常来自任务完成率与答案准确性。官方 leaderboard 指标对答案做“列向量匹配”，列名不参与评测，额外预测列也不扣分，因此系统目标应优先保证关键列值的正确性并避免评测超时。当前系统的主要风险点是：Token 预算按字符粗估导致 context 溢出、重复 schema/SQL 计算浪费时间与 token、错误重试策略过于单一导致超时或失败，以及工具描述噪音过大影响决策稳定性。面向 `https://dataagent.top/` 的无人值守运行特性，本次优化以“稳定 + 低风险 + 可验证”为优先，不追求架构复杂度。

## What Changes
- Token budget 精确计算：从字符预算升级为 token 预算，按模型动态控制上下文，减少因超限导致的失败。
- 缓存层（TTL + LRU）：缓存 schema/采样、schema 检索中间结果、重复只读 SQL 结果、embedding 结果，降低重复调用成本。
- 错误模式识别 + 策略切换：从“连续相同错误”升级到可解释的错误分类与风暴检测，减少无效重试导致的超时/失败。
- 工具按阶段动态加载：按能力分组工具，并在 prompt 中只注入必要工具，降低噪音并提升稳定性。
- Embedding batch 与可配置：对检索场景进行 batch，并允许通过配置切换 embedding 模型或降级策略。
- 并发安全：共享资源（缓存、embedding client）线程安全，维持每任务状态隔离。
- 延后项（非本次目标）：完整 MCP 协议实现、Supervisor 多 Agent 编排、跨任务持久化记忆（除缓存/cheat sheet 之外）。

## Impact
- Affected specs: Prompt Budgeting, Tooling, Tool Registry, Context Management, Caching, Concurrency, Testing.
- Affected code:
  - `src/data_agent_baseline/tools/*`（工具定义、注册、动态加载）
  - `src/data_agent_baseline/orchestrators/*`（错误策略与重试决策的接入点）
  - `src/data_agent_baseline/runtime/*`（上下文压缩、policy、state、memory、trace）
  - `src/data_agent_baseline/config.py`（token 预算、embedding 配置、缓存配置）

## ADDED Requirements
### Requirement: 工具分组与按需加载
The system SHALL provide 工具分组与动态加载能力，以减少 Token 占用并提升决策稳定性。

#### Scenario: 阶段化工具注入
- **WHEN** 进入不同任务阶段（如：探索/定位 schema/编写 SQL/执行与校验/收敛提交）
- **THEN** 仅注入当前阶段必要的工具描述与 schema
- **AND THEN** 未使用的工具在 prompt 中被裁剪或延后

#### Stage → Tool Group 映射（实现约束）
| 阶段 | 目标 | 必须可用工具组 | 禁止/尽量不注入 |
|---|---|---|---|
| 探索 (Explore) | 快速理解数据包结构与文件入口 | `data_exploration`（list_context/read_* / get_data_schema / extract_dataframe_stats） | `code_execution`（除非明确需要） |
| Schema 定位 (Schema Map) | 将自然语言实体映射到表/字段 | `schema_mapping`（inspect_sqlite_schema / retrieve_schema_context） | `code_execution` |
| 编写 SQL (Draft SQL) | 生成候选 SQL 与验证假设 | `schema_mapping` + `code_execution`（execute_context_sql/execute_duckdb_sql） | `data_exploration`（保持最小） |
| 执行与校验 (Execute & Validate) | 运行 SQL/代码并校验结果格式 | `code_execution` + 最小必要的 `data_exploration` | `knowledge_retrieval`（除非需要规则解释） |
| 收敛提交 (Finalize) | 组装最终 AnswerTable，避免再开新分支 | `planning_debugging`（仅包含 answer/update_plan 等低噪音工具） | 任何高成本探索工具 |

### Requirement: 缓存层（TTL + LRU）与可复用结果
The system SHALL provide 一个可配置的缓存层，用于降低重复计算与重复工具调用成本，并减少 token 与时间开销。

#### Scenario: Schema/采样缓存
- **WHEN** 同一任务周期内多次请求同一数据源的 schema/采样
- **THEN** 系统命中缓存并直接返回结果

#### Scenario: 重复 SQL 结果缓存
- **WHEN** 多次执行完全一致的只读 SQL（同一路径与同一 SQL 文本与同一 limit）
- **THEN** 系统优先返回缓存结果（在 TTL 内）

#### Scenario: 缓存失效（任务生命周期）
- **WHEN** 单个任务结束（成功或失败）
- **THEN** “任务级缓存”（该任务上下文内的临时缓存）必须被释放或清空
- **AND THEN** “跨任务缓存”（仅限安全、与具体任务数据无关或可安全复用的条目）默认关闭；如启用必须设置 TTL 与大小上限

#### Scenario: 缓存失效（数据变更）
- **WHEN** schema/采样/SQL 结果的输入数据源发生变化（至少包含：文件路径变化，或同路径文件的 mtime/size 变化）
- **THEN** 对应缓存 key 必须变化或旧条目被主动失效
- **AND THEN** 不允许复用旧 schema/采样/SQL 结果到新版本数据源

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
- **THEN** 系统触发策略切换（如：回退到 schema 检索、生成更小的探索 query、缩小数据范围、或更换执行引擎）

#### Scenario: 兜底（策略耗尽）
- **WHEN** 已触发所有可用策略切换且仍失败，或达到最大重试次数
- **THEN** 系统必须停止进一步重试，避免超时
- **AND THEN** 将失败原因以结构化错误写入可观测状态（用于 trace/日志与后续分析）
- **AND THEN** 若已存在“可提交的候选答案”（即便不确定），系统应优先提交候选答案而不是继续探索；若没有候选答案，则提交一个结构化的失败占位答案（例如额外增加 `error` 列；额外列不影响评测）以保证流程终止而非超时

### Requirement: 任务级超时控制与预算驱动降级
The system SHALL enforce 任务级时间预算与工具级超时，并在临近超时时进行可解释的降级与收敛，以提高无人值守场景的任务完成率。

#### Scenario: 任务级超时
- **WHEN** 单任务运行时间达到配置的 `task_timeout_seconds`
- **THEN** 系统必须终止该任务的进一步工具调用与 LLM 推理，并进入收敛流程（finalize）

#### Scenario: 临近超时的降级策略
- **WHEN** 剩余时间低于阈值（例如：剩余 < 10% 或 < N 秒）
- **THEN** 系统必须禁止高成本动作（如大范围探索、长文档全文检索、重复多轮改写）
- **AND THEN** 优先执行低成本校验与答案组装，并尽快提交

### Requirement: 并发安全与任务隔离
The system SHALL ensure 每个任务拥有独立 RuntimeState，而共享资源（embedding client、缓存）具备明确的线程安全实现。

#### Scenario: 并发运行多个任务
- **WHEN** RunConfig.max_workers > 1 并行运行任务
- **THEN** 不同任务的上下文、记忆与步骤记录互不污染
- **AND THEN** embedding client 使用单例实例并由 `threading.Lock` 保护其关键调用路径
- **AND THEN** 缓存采用 `TTLCache`/`LRUCache` 并由 `threading.Lock` 包装访问，避免竞态与脏读

## MODIFIED Requirements
### Requirement: 上下文管理（从字符预算升级为 token 预算）
The system SHALL replace `max_prompt_chars` 为 `max_prompt_tokens`（按模型动态计算），并在构建 prompt 时严格遵循 token 预算。

## REMOVED Requirements
### Requirement: 全量工具描述预注入
**Reason**: 预加载所有工具导致 Token 浪费、上下文易溢出且扩展困难。
**Migration**: 引入“工具分组 + 按需加载”的工具描述策略，在 prompt 中暴露最小必要集合。
