# KDD Cup Data Agent Optimization Plan

## 核心痛点回顾
当前的 Data Agent 在面对复杂任务（特别是 Hard/Extreme 难度）时，存在三个核心缺陷：
1. **知识忽略与盲猜（Knowledge Deficit & Blind Guessing）**：模型常常跳过文档读取，直接凭借先验知识（如英文直译捷克语）在 SQL 中盲猜分类值，导致返回 0 行。
2. **缺乏语义裁决（No Semantic Adjudication）**：单纯的检索（无论是 BM25 还是 Embedding）只能“找到候选知识”，但缺乏机制去验证这些知识在实际数据库中是否存在（例如：检索到规则说“状态=活跃”，但数据库实际存的是“status=1”）。
3. **子代理角色模糊（Underutilized Subagent）**：虽然代码库中存在 LangGraph 隔离的子代理（`delegate_to_data_analyst`），但其触发是“被动的”，且职责不够清晰（仅仅是“帮主代理写代码”），未能发挥其作为独立“数据探查官”的潜力。

---

## 总体架构重构目标
将 Data Agent 从“可选查资料的单体问答机”升级为**“强制查证的流水线系统（Pipeline of Experts）”**。
核心链路固定为：`Query → Retrieve (混合检索) → Adjudicate (语义裁决/对齐) → Reason & Generate (生成SQL) → Answer`。

---

## 具体的优化阶段与任务清单 (Action Items)

### Phase 1: 底层工具链的强化与防呆 (已部分完成)
**目标：阻断最底层的“盲猜”路径，提升检索与探查的精确度。**

- [x] **1. 增强 `get_data_schema` (EDA 探查)**：修改 `filesystem.py`，在提取表结构时，自动对 VARCHAR/INTEGER 列执行 `SELECT DISTINCT ... LIMIT 10`。让模型在查表时强制看到真实的数据样本。
- [x] **2. 引入 Hybrid RAG (`search_knowledge_doc`)**：新增专门的知识检索工具，按 Markdown 标题切块。对于 Easy/Medium 任务使用纯 BM25；对于 Hard/Extreme 任务使用 BM25 + Embedding + RRF 重排，解决纯词频匹配的语义缺失问题。
- [x] **3. 强化主 Prompt (`REACT_SYSTEM_PROMPT`)**：在系统提示词中增加“MANDATORY KNOWLEDGE CHECK”和“ZERO-RESULT REFLECTION”的强约束。

### Phase 2: 构建“语义裁决层 (Semantic Adjudication Layer)” (已完成)
**目标：解决“检索到了规则，但与数据库 Schema 不匹配”的致命问题。这也是你指出的系统结构核心缺失。**

- [x] **1. 重塑 Subagent 职责**：修改 `prompt.py` 中的 `SUBAGENT_SYSTEM_PROMPT`。将子代理的定位从“Data Analyst”改为“**Semantic Validator (语义校验官)**”。它的唯一任务是：拿着主代理检索到的业务规则，去数据库里探查，验证规则中的实体到底对应哪张表的哪个列、哪个确切的枚举值。
- [x] **2. 强制的前置委派机制 (Hard Gate)**：修改 `plan_execute_graph.py` 中的 Planner 逻辑。在生成执行计划前，**在代码层面强制调用（Hard Gate） `delegate_to_data_analyst` 进行语义裁决**。主代理不能在没有拿到“校验过的映射字典（Mapping Dict）”前就去写 SQL。不再依赖 Prompt 约束，而是通过 LangGraph 的结构硬性保证。
- [x] **3. 规范裁决输出格式**：修改 `subagent_finish` 工具的契约。要求子代理必须返回结构化的 `adjudicated_rules`（例如：明确指出“现金取款”对应 `trans.csv` 的 `operation='VYBER'`），主代理拿到这个报告后，直接“填空”写 SQL，彻底杜绝幻觉。

### Phase 3: Schema-Linking 索引化（对齐主流 Text-to-SQL）(待执行)
**目标：把“检索”从泛用 RAG 升级为“Schema 检索 + 链接 + 约束生成”，减少噪声并提升 Hard/Extreme 的一跳命中率。**

主流高分 Text-to-SQL 系统通常遵循：`Question → Schema Linking → (SQL Generate) → Execute/Repair`。这里的关键不是“检索到更多文本”，而是**把问题中的实体/指标/约束，精确绑定到数据库里的表/列/枚举值**。

- [ ] **3.1 统一 Schema Catalog（SSOT）**：为每个任务 context 生成结构化的 SchemaCatalog（表/文件/列/类型/样例值/主外键/候选 join 边）。
  - 数据来源：`inspect_sqlite_schema`、`get_data_schema`、以及 DuckDB 对文件的 `DESCRIBE` / `SELECT DISTINCT ... LIMIT N` 探测结果
  - 产物建议：`schema_catalog.json`（task_output_dir 或 logs 目录，便于复盘）
- [ ] **3.2 Schema Index（BM25 + Embedding 可选）**：把 SchemaCatalog 转成可检索的“schema 文档块”，并建立索引。
  - 文档块粒度：Table-level / Column-level（建议 Column-level 为主，包含 table/column/type + top distinct values + 别名）
  - 检索目标：给定问题，返回 Top-K 候选表/列/值（而不是直接返回业务文档片段）
- [ ] **3.3 链接与约束生成（Constrained Context）**：将 Top-K schema 结果作为“候选工作集”，强制限制模型只能在该工作集中写 SQL。
  - 机制：渲染 prompt 时仅暴露候选表/列（以及必要的 join 边），避免全库曝光导致的“想当然 join”
  - 和 Phase 2 关系：Semantic Validator 优先从 schema index 里定位候选列，再用 DISTINCT probing 做最终裁决，产出 Verified Mapping
- [ ] **3.4 Join Path Suggestion（可解释 join 图）**：为候选表集自动生成 1-3 条最短 join 路径建议，提供给生成器。
  - SQLite：基于外键（若存在）+ 关键列名匹配（如 `*_id`）+ 样例值重合度（轻量）
  - 文件/无外键：基于列名相似度 + 候选键的 distinct/unique 统计（来自 EDA）
- [ ] **3.5 Execution-Guided Repair（执行反馈闭环）**：将 “执行报错/0 行” 明确分流为可修复类型，并基于 SchemaCatalog 给出下一步动作建议。
  - 报错类型：列不存在/表不存在/类型不匹配/join 爆炸/0 行（过滤值不对）
  - 下一步动作：回到 3.2/3.4 重新链接，或触发 Semantic Validator 二次探测

### Phase 4: 本地评测环境的修复与打榜验证 (待执行)
**目标：确保我们能真实量化每一次架构调整带来的分数收益。**

- [ ] **1. 修复官方评测脚本 Bug**：我们之前发现官方的 `kdd_eval.py` 在对比 DataFrame 时存在严重的列名强校验 Bug（导致本该对的题判为 0 分）。需要把本地修复版（支持 `Full Name` 组合和无序列匹配）彻底固化到跑测流程中。
- [ ] **2. 修复本地数据集路径**：解决刚才在 CLI 中遇到的 `Missing task.json` 和目录挂载路径不一致的问题，确保 `run-benchmark` 能顺畅跑完所有公开的 50 道 Demo 题。
- [ ] **3. 端到端 A/B 测试**：在完成 Phase 1 和 Phase 2 的重构后，选取极具代表性的 Task 38（捷克语枚举分类）和 Task 80（跨表实体歧义），跑出具体的 `trace.json`，验证“语义裁决层”是否成功拦截了幻觉。

---

## 我们现在的进度对齐

你之前说得非常透彻：**“本质问题不在检索策略，而在系统结构。检索解决‘找到什么’，而正确性依赖‘如何验证’。”**

这份 Plan 的核心灵魂就是 **Phase 2（语义裁决层）**。我们不再纠结于用什么高级的 Embedding 模型去大海捞针，而是通过架构层面的“强校验”，逼迫大模型在生成 SQL 前，必须拿到由 Subagent 签发的“数据库实体映射通行证”。

下一步最可能带来稳定提分的方向是 **Phase 3（Schema-Linking 索引化）**：把“找资料”变成“找 schema”，让生成 SQL 的上下文从开放式变成受控式。

---

## 修改历程与踩坑记录（便于复盘）

### 架构层面的关键决定
1. **从 Prompt 约束迁移到 Hard Gate**：将“必须先做语义裁决”从提示词规则升级为代码层面的强制前置阶段（LangGraph / orchestrator 结构保证）。
2. **先验证、后生成**：把“生成 SQL”从第一动作推迟到“拿到 Verified Mapping 之后”，显著降低 0 行与枚举值幻觉。

### 工程实现中的典型坑
1. **镜像提交形式**：比赛规则要求提交可运行的完整镜像（而不仅是 Dockerfile），需要提供镜像导出/打包流程。
2. **外部网络/模型网关限制**：在受限网关环境下，某些模型/路由可能返回鉴权失败或不可用，需要把模型与 key/base_url 全部做成环境变量注入，并在配置中容错。
3. **DuckDB 扩展与离线问题**：在线自动安装扩展可能在评测环境超时或失败，需要优先走预装/离线加载路径，避免运行期联网依赖。
4. **大上下文导致的“看起来懂了”**：把整份 schema/文档都塞进 prompt 会导致模型过拟合到错误 join 直觉，Schema-Linking 的“候选工作集”是必要的上下文收敛手段。
