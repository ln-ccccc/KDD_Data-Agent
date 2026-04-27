# KDD Cup Data Agent Optimization Plan

## 核心痛点回顾
当前的 Data Agent 在面对复杂任务（特别是 Hard/Extreme 难度）时，存在三个核心缺陷：
1. **知识忽略与盲猜（Knowledge Deficit & Blind Guessing）**：模型常常跳过文档读取，直接凭借先验知识（如英文直译捷克语）在 SQL 中盲猜分类值，导致返回 0 行。
2. **缺乏语义裁决（No Semantic Adjudication）**：单纯的检索（无论是 BM25 还是 Embedding）只能“找到候选知识”，但缺乏机制去验证这些知识在实际数据库中是否存在（例如：检索到规则说“状态=活跃”，但数据库实际存的是“status=1”）。
3. **子代理角色模糊（Underutilized Subagent）**：虽然代码库中存在 LangGraph 隔离的子代理（`delegate_to_data_analyst`），但其触发是“被动的”，且职责不够清晰（仅仅是“帮主代理写代码”），未能发挥其作为独立“数据探查官”的潜力。

### 4. 为什么还要用看似“落后”的 BM25？纯 Embedding 不够吗？
**疑问**：在 LLM 时代，BM25 这种基于词频统计的古老算法是不是太落后了？是不是因为性能妥协才用的？
**技术研判**：**完全不是妥协，BM25 在 Text-to-SQL 领域是不可或缺的 SOTA 标配。**
- **Embedding 的致命缺陷（过度泛化）**：Dense Embedding（稠密向量）擅长捕捉“语义”。当用户说“利润”时，它能帮你找到 `revenue` 或 `net_income`。但当用户寻找特定专有名词、UUID、商品代号（如 `SKU-9982`）、或者奇怪的数据库缩写时，大模型的 Tokenizer 往往会把这些词切碎，导致向量在空间中丢失精确匹配的能力。
- **Text-to-SQL 的极高精确度要求**：SQL 生成是一个“差之毫厘，执行报错”的任务。如果用户问的是“查找状态为 `ACTV` 的用户”，Embedding 可能会因为语义相近把 `status='ONLINE'` 召回排在前面，而 BM25 会因为 `ACTV` 这个罕见词的 TF-IDF 极高，死死把它钉在 Top 1。
- **Hybrid RAG 的本质（双通道互补）**：业界最先进的 Schema Linking 系统（包括 BIRD 等高难度榜单的前排方案）清一色采用 **BM25（负责硬匹配/精准兜底） + Dense Embedding（负责软匹配/语义泛化）**，最后通过 RRF（Reciprocal Rank Fusion）等算法进行融合重排。BM25 提供极高的 Precision（精确率），Embedding 提供极高的 Recall（召回率），两者缺一不可。

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

### Phase 3: Schema-Linking 索引化 (Phase 3B - 贴近主流 Text-to-SQL Pipeline) (待执行)
**目标：将检索从“面向文档的 RAG”升级为“面向 Schema 的受控检索”，通过本地 Embedding 和结构化索引，为 SQL 生成提供精准的上下文（工作集），降低大模型幻觉。**

- [ ] **1. 构建 SchemaCatalog (SSOT)**：编写脚本/工具，在任务初始化时，遍历 `context/` 下的所有表（SQLite, CSV, Parquet），提取表名、列名、数据类型，并自动执行 `SELECT DISTINCT` 采样枚举值，生成全局唯一的 Schema 视图。
- [ ] **2. 本地 Schema Index 向量化**：使用预置的本地 Embedding 模型（如 `Alibaba-NLP/gte-Qwen2-1.5B-instruct`），将 SchemaCatalog 中的“表描述”和“列描述+枚举值”转化为向量索引。该 1.5B 模型具备极强的指令微调语义理解能力，足以应对复杂中英映射。
- [ ] **3. 混合检索与 Top-K 裁剪**：在生成 SQL 前，将用户的 Query 通过 BM25（词法精准匹配） + Embedding（语义映射，如“利润”对齐“revenue”）进行混合检索，召回 Top-K 相关的表和列，构建成 Constrained Schema Context。
- [ ] **4. 剥夺“全库裸写”权限**：修改大模型的 SQL 生成 Prompt，强制其只能使用检索召回的 Top-K 表和列，彻底斩断其“凭空捏造表名”的路径。

### Phase 4: 本地评测环境的修复与打榜验证 (待执行)
**目标：确保我们能真实量化每一次架构调整带来的分数收益。**

- [ ] **1. 修复官方评测脚本 Bug**：我们之前发现官方的 `kdd_eval.py` 在对比 DataFrame 时存在严重的列名强校验 Bug（导致本该对的题判为 0 分）。需要把本地修复版（支持 `Full Name` 组合和无序列匹配）彻底固化到跑测流程中。
- [ ] **2. 修复本地数据集路径**：解决刚才在 CLI 中遇到的 `Missing task.json` 和目录挂载路径不一致的问题，确保 `run-benchmark` 能顺畅跑完所有公开的 50 道 Demo 题。
- [ ] **3. 端到端 A/B 测试**：在完成 Phase 1 和 Phase 2 的重构后，选取极具代表性的 Task 38（捷克语枚举分类）和 Task 80（跨表实体歧义），跑出具体的 `trace.json`，验证“语义裁决层”是否成功拦截了幻觉。

---

## 修改历程、踩坑记录与关键思考

### 1. 为什么放弃单纯的 Prompt 约束，转向 Hard Gate？
**踩坑复盘**：最初，我们试图在主代理的 `REACT_SYSTEM_PROMPT` 中加入严厉的警告（如 "MANDATORY KNOWLEDGE CHECK", "ZERO-RESULT REFLECTION"），期望大模型在遇到捷克语（如“现金取款”对应 `VYBER`）或复杂业务公式时，能主动去查文档或探查数据库。
**结果**：对于 GPT-4 级别的大模型，在 Easy 难度下表现尚可。但一旦面对 Hard/Extreme 任务，或者上下文变长时，模型极易产生“过度自信（Overconfidence）”，直接跳过工具调用，用常识盲猜 SQL 过滤条件，导致频繁返回 0 行。
**思考与破局**：在企业级 Data Agent 中，**流程的可靠性必须高于大模型的自觉性**。我们因此引入了 Phase 2 的 **Hard Gate**，在 LangGraph 架构层面将 `delegate_to_data_analyst`（语义校验官）的执行固化为生成 SQL 的绝对前置条件。没有拿到校验过的 Mapping Dict，主代理根本见不到写 SQL 的入口。

### 2. 为什么坚持使用本地的 Qwen2-1.5B Embedding 模型？它够用吗？
**疑问**：在 Text-to-SQL 领域，1.5B 的 Embedding 模型会不会太“弱”，导致 Schema Linking 失败？
**技术研判**：
- **绝对不弱，甚至是“甜点级”的 SOTA 选择**：`Alibaba-NLP/gte-Qwen2-1.5B-instruct` 基于 Qwen2 大模型底座进行指令微调。在 Schema Linking 任务中，普通的相似度模型（如 300M 级别的 bge）难以跨越自然语言（NL）与数据库缩写/专有名词之间的“语义鸿沟”。而 1.5B 模型具备了近似 LLM 的逻辑理解能力。
- **长上下文支持（32k）**：企业数据库的 DDL 动辄几千上万 Token，传统 512 Token 的 Embedding 模型会直接截断，而该模型可以完整吃下整张表的结构描述。
- **离线可控性**：由于 KDD Cup 的评测环境极其封闭（限制外网访问），在 Docker Image 中打包一个 1.5B 的模型（约需 3-4GB 内存/显存）是性能与资源约束下最完美的平衡。它能提供接近顶级闭源 API（如 OpenAI text-embedding-3）的密集检索能力。
- **改进策略**：单纯的 Dense Retrieval 容易忽略精准的 ID 或单字匹配。因此我们在架构中配合了 BM25（词法检索），形成了 Hybrid RAG 方案。

### 3. RAG 在 Text-to-SQL 提分中的真实定位
**关键思考**：RAG 到底是不是提分的关键？
**结论**：**RAG 是保分的基础，Schema Linking 才是提分的上限。**
- **RAG 的作用（防止下限过低）**：应对 `knowledge.md` 中的业务口径定义（例如：“有效用户是指近 30 天登录且余额 > 0 的用户”）。没有 RAG，模型连规则都不知道，必然写错。
- **Schema Linking 的作用（拉高上限）**：即使 RAG 找对了规则，如果不能把“余额”精准映射到 `accounts.balance_usd`，SQL 依然会跑崩。
- **主流范式启示**：高分榜单上的系统（如 Spider 数据集上的 SOTA）往往在 RAG 之后，有一个极重的 **对齐层（Alignment / Grounding）**。我们当前的重构方向（Hard Gate 探测 + 结构化 Schema Index）正是为了补齐这决定胜负的“最后一步”。

---
