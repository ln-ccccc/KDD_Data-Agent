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

### Phase 3: 检索策略的进阶与防噪 (待执行)
**目标：解决 Easy/Medium 难度下的检索降级问题，防止同义词扩展引入噪声。**

- [ ] **1. 大纲导航阅读 (TOC-based Exact Reading)**：为短文档（Easy/Medium）新增一个极轻量级的工具 `list_knowledge_headings`。大模型先看目录（如 `['# User Status', '# Error Codes']`），再按需调用 `read_knowledge_section` 精确读取特定段落。这完全绕过了 BM25/Embedding 的匹配缺陷。
- [ ] **2. 优化 Query Rewrite**：在主代理调用检索工具前，引导其将用户的自然语言 Query 提取为核心实体，而不是盲目发散。扩展词仅作为 Hybrid RAG 中的次要召回权重，确保检索不偏离主轴。

### Phase 4: 本地评测环境的修复与打榜验证 (待执行)
**目标：确保我们能真实量化每一次架构调整带来的分数收益。**

- [ ] **1. 修复官方评测脚本 Bug**：我们之前发现官方的 `kdd_eval.py` 在对比 DataFrame 时存在严重的列名强校验 Bug（导致本该对的题判为 0 分）。需要把本地修复版（支持 `Full Name` 组合和无序列匹配）彻底固化到跑测流程中。
- [ ] **2. 修复本地数据集路径**：解决刚才在 CLI 中遇到的 `Missing task.json` 和目录挂载路径不一致的问题，确保 `run-benchmark` 能顺畅跑完所有公开的 50 道 Demo 题。
- [ ] **3. 端到端 A/B 测试**：在完成 Phase 1 和 Phase 2 的重构后，选取极具代表性的 Task 38（捷克语枚举分类）和 Task 80（跨表实体歧义），跑出具体的 `trace.json`，验证“语义裁决层”是否成功拦截了幻觉。

---

## 我们现在的进度对齐

你之前说得非常透彻：**“本质问题不在检索策略，而在系统结构。检索解决‘找到什么’，而正确性依赖‘如何验证’。”**

这份 Plan 的核心灵魂就是 **Phase 2（语义裁决层）**。我们不再纠结于用什么高级的 Embedding 模型去大海捞针，而是通过架构层面的“强校验”，逼迫大模型在生成 SQL 前，必须拿到由 Subagent 签发的“数据库实体映射通行证”。

你觉得这份 Plan 的思路和优先级排序是否符合你的预期？如果没问题，我们可以直接进入 **Phase 2.1**，开始重写 Subagent 的提示词和核心契约！