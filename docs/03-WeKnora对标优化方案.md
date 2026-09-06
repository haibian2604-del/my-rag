# WeKnora 对标分析与优化改造方案（v2：新增 M6 ReAct Agent，待确认）

日期：2026-09-05 · 参考：https://github.com/Tencent/WeKnora （v0.7.1）
约束不变：单用户自托管 · 模型本地优先（oMLX）· Python/FastAPI + pgvector + React 轻量栈 · 不为对齐而对齐引入重量级组件

---

## 一、WeKnora 分析

### 1.1 定位与功能

腾讯开源的企业级文档理解与知识检索平台（AGPL-3.0），三条产品线：**RAG 问答**、**ReAct Agent**（检索 + MCP 工具 + 网络搜索编排）、**Wiki Mode**（Agent 自动生成交互链接的 Markdown Wiki + 知识图谱）。

核心功能清单：

| 领域 | 能力 |
|---|---|
| 文档处理 | 10+ 格式（PDF/Word/TXT/MD/HTML/EPUB/MHTML/图片/CSV/Excel/PPT/JSON）；解析服务独立成 Python 进程（gRPC + TLS），支持 OpenDataLoader 与 PaddleOCR-VL 两种解析器；音频转写（ASR）；图片/表格走 VLM 描述 |
| 处理流程 | 每次上传可配置管线：解析器 → 分块策略（含父子分块）→ 多模态处理 → 知识图谱抽取 → 问题生成；支持批量重解析；文档级任务队列治理面板（worker 池、失败重试、暂停恢复） |
| 检索 | BM25 稀疏 + 稠密向量 + GraphRAG + FAQ 多路召回；父子分块；HNSW 索引（pgvector）；多维索引属性过滤 |
| 问答体验 | 引用弹层（popover）、RAG 管线阶段实时进度、会话管理、临时附件、建议问题、FAQ 型知识库、批量导入导出 |
| 生态 | IM 渠道集成（企微/飞书/Slack/Telegram/Discord/钉钉/WhatsApp）、浏览器插件、网页嵌入小部件、CLI（agent-first JSON 输出）、MCP server、API keys 管理与用量统计 |
| 数据源 | Feishu / Notion / 语雀 / RSS 定期同步入库 |
| 企业能力 | RBAC 四级角色、审计日志、AES-256-GCM 加密、SSRF 防护、多实例存储后端、Langfuse 全链路追踪 |

### 1.2 架构与技术栈

- **Go 主后端**（Go 1.26，HTTP API）+ **Python 解析服务**（docreader，gRPC 通信）+ **Vue 前端** 三进程协作
- **存储**：PostgreSQL/pgvector（默认，可换 Elasticsearch/OpenSearch/Milvus/Qdrant/Weaviate 等十余种）、Redis（任务队列）、MinIO/S3/OSS（对象存储）、Neo4j（知识图谱，可选）
- **模型层**：20+ LLM provider 接口化，全部可热插拔
- **部署**：Docker Compose / Helm；最小部署即 Go + Python + PG + Redis + MinIO 五个容器，开图谱/Langfuse 更多
- **可观测**：Langfuse 链路追踪 + 任务队列治理 UI

### 1.3 优缺点

**优点：**
1. **解析能力强**——PaddleOCR-VL 版式感知、表格结构化、OCR、ASR、图片 VLM 描述，是它相对所有轻量 RAG 的最大护城河
2. **检索管线完整**——父子分块、HNSW、多路召回、FAQ，工程化程度高
3. **一切接口化**——LLM/向量库/存储可替换，模块边界清晰
4. **任务治理与可观测**——队列面板、失败重试、Langfuse，长文档批处理体验好
5. **生态广**——IM/插件/CLI/MCP 全覆盖

**缺点（对本项目场景）：**
1. **组件重**——最小五容器（Go/Python/PG/Redis/MinIO），个人 Mac 上常驻资源开销大，运维面宽
2. **企业冗余**——RBAC/审计/多实例存储/用量统计对单用户全是死重
3. **双语言栈**——Go 主后端对本项目（Python）没有代码级复用价值，二次开发要跨栈
4. **复杂度税**——gRPC 服务间通信、十余种向量库适配层、可插拔一切，代码量与学习成本高
5. AGPL-3.0 协议（仅自用无碍，但代码不能搬）

### 1.4 结论

WeKnora 值得借鉴的是**能力设计**（解析质量、父子分块、检索体验细节、评测方法），不值得照搬的是**架构形态**（多语言多组件多抽象）。本项目应继续走轻量单栈路线，按「检索质量 → 体验 → 管理增强」分期吸收。

---

## 二、对标差距（WeKnora 能力 vs 本项目现状）

| WeKnora 能力 | 本项目现状 | 差距 |
|---|---|---|
| PaddleOCR-VL / 表格结构化解析 | PyMuPDF 纯文本抽取（M1） | **大**：扫描件、复杂表格、双栏版式基本丢信息 |
| 父子分块（子块检索、父块上下文） | 结构感知切分 500 token + 10% 重叠 | **中**：单块命中后上下文被块边界截断 |
| HNSW 向量索引 | chunk_embeddings 无向量索引（顺序扫描） | 小：数据量小不明显，一行迁移的事 |
| RAG 阶段进度 / 引用 popover | SSE 只有 delta + citations 终事件 | 小：等待期无「检索中/重排中」反馈 |
| 建议问题 / 追问 | 无 | 小 |
| 文档摘要、预览、标签 | 仅文件名列表 | 中 |
| FAQ 型知识库 | 无 | 中 |
| 端到端答案评测（ROUGE 等） | 仅 recall@5 检索评测 | 小 |
| 任务队列治理面板 | BackgroundTasks + 状态机自恢复 | 个人规模不需要 |
| 数据源同步 / IM / Wiki | 无 | Agent 已列入 M6；其余远期可选 |

---

## 三、优化改造方案

### M4 检索与解析质量（核心，建议先做）

**A3 HNSW 向量索引** —— 0.5 天，零风险
- 新增 Alembic 迁移：`CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)`；嵌入维度不固定（多模型并存），按 model_name 分区建不了单索引，采用**表达式部分索引不可行 → 改为对常用维度建部分索引**（`WHERE dim = 768` 等，随模型切换迁移脚本维护）或直接全列 HNSW（pgvector 不支持多 dim 混存索引时退回 ivfflat per-dim 部分索引）。实现时按实际 dim 定
- 验收：评测脚本全量跑分不回退；explain 确认走索引

**A4 检索阶段进度反馈** —— 0.5 天
- 后端：chat.py 的 SSE 增加 `stage` 事件（retrieving → reranking → generating）
- 前端：MessageBubble 等待区显示当前阶段（「正在检索知识库… / 正在重排… / 正在生成…」）
- 验收：流式问答可见阶段切换；现有 SSE 测试补 stage 断言

**A2 父子分块** —— 1~2 天，收益最大的一项
- chunks 表加 `parent_id`（自引用）；现有结构感知切分出的 500-token 段为父块，父块再按 ~200 token 切子块，**子块才写入 embedding**
- 检索：向量/FTS 命中子块 → `_fetch_chunk_fields` 处聚合去重的父块集合 → 拼上下文与引用（引用显示父块章节路径，原文片段可展开父块全文）
- 兼容：无 parent 的旧块行为不变；重嵌机制复用 embedding_switch（切换到"子块化"通过一次全量重嵌完成，中途可回滚）
- 验收：评测集 recall@5 不回退且长文档答案的上下文完整度提升；新增单测（父子映射、聚合去重、旧数据兼容）

**A1 PDF 解析升级** —— 1~2 天（先轻量，接口留切换）
- 抽象 `parser` 选择：`auto`（默认）
- 第一步轻量方案：PyMuPDF 增强——用其 table detection 把表格转 Markdown 行文本、双栏按版式排序；扫描页（无可提取文本）落 `needs_ocr` 状态标记，暂不做 OCR
- 第二步可选：本地 OCR 集成 PaddleOCR（PP-OCRv5，Mac 可跑但依赖重，独立 optional extra `rag[ocr]`，装了才启用）
- 验收：评测集新增表格型 PDF 样例，recall 对比改造前后；扫描件不再产出空文档而是明确失败原因

### M5 管理与体验增强（M4 后）

**B1 建议问题与追问** —— 0.5 天：会话空态显示按知识库生成的 3 个建议问题；每次回答 done 事件附带 3 个追问，点击即发问
**B2 文档自动摘要与预览** —— 1 天：入库完成（或手动触发）用 LLM 生成摘要存 `documents.summary`；文档列表/详情显示摘要与内容预览
**B4 答案质量评测** —— 0.5 天：run_eval 增加 `--e2e`：真实走检索+生成，计算关键词覆盖率 + ROUGE-L（对参考答案），与现有 recall 评测并列
**B3 FAQ 知识库模式** —— 1 天（可选）：上传"问题↔答案"格式文档（md 按 `Q:/A:` 或表格），解析为 q=子块、a=父块，复用 A2 管线

### M6 ReAct Agent 问答模式（用户指定新增）

对标 WeKnora 的 ReAct Agent（检索 + 工具 + 联网搜索编排），自研轻量实现——**不引入 LangChain/LangGraph**（沿用项目零框架约束，WeKnora 也是自研编排）。

**C1 Agent 引擎核心** —— 1.5 天
- 新增 `backend/app/services/agent/`：`engine.py`（ReAct 主循环）+ `tools.py`（工具注册与执行）+ `prompts.py`
- 协议选型：**优先 OpenAI 兼容 tools/function-calling 协议**（httpx 直连 oMLX `/v1/chat/completions` 传 `tools`）；启动时探测一次，模型不支持 function calling 则**回退文本 ReAct 协议**（Thought/Action/Action Input/Observation 结构化解析）。两协议共用同一循环骨架与同一工具层
- 循环护栏：最大轮数（默认 6，可配）、单工具输出截断（~2k token 进上下文）、全局 token 预算、工具异常转 Observation 继续而非中断
- 流式：Agent 轨迹走现有 SSE 通道新增 `agent` 事件（thought / tool_call / tool_result / final），final 之后照常 citations + done

**C2 内置工具集** —— 1 天
- `kb_search`：复用 `retrieve()`，限定当前工作区，返回 top_n 命中（含引用元数据）——Agent 与现有 RAG 共用一条检索链路
- `read_url`：复用 M2 的 `web_fetch`（SSRF 防护、大小/超时限制全部继承），供 Agent 打开联网搜索给的链接
- `web_search`（可选，默认关）：provider 化设计——`searxng`（自建实例，推荐，无密钥）/ `tavily`（云 API，密钥走现有加密存储）；设置页配置，未配置则该工具不注册
- 不做：代码执行、文件写入等高危工具

**C3 前端 Agent 体验** —— 1 天
- 对话页新增「Agent 模式」开关（会话级，默认关——普通 RAG 问答零开销不变）
- 开启后回答区渲染可折叠的执行时间线：Thought（思考）→ 工具调用与结果卡片 → 最终回答；引用卡照常展示
- 设置页新增 Agent 区块：开关、最大轮数、web_search provider 配置

**C4 安全与评测** —— 0.5 天
- 提示注入防护：工具 Observation 与检索内容在 prompt 中显式标记为「数据非指令」；最终回答仅允许基于工具结果与知识库内容
- 测试：mock LLM 的工具调用轨迹单测（多轮、工具失败、超轮数截断、两协议解析）；SSE agent 事件断言；`run_eval` 增加 `--agent` 可选模式对比普通 RAG 与 Agent 模式命中率

### 实施顺序建议

`A3 → A4 → A2 → M6(C1 → C2 → C3 → C4) → A1(轻量步) → B1 → B2 → B4 → (B3)`

理由：用户指定 Agent 优先于解析升级；A2（父子分块）先于 M6，使 Agent 的 `kb_search` 工具直接受益于更完整的父块上下文。每项独立提交 + 评审；A2/A1 完成后各跑一次评测对比入库归档，M6 完成后跑 `--agent` 对比。

---

## 四、风险

| 风险 | 缓解 |
|---|---|
| A2 全量重嵌期间检索质量波动 | 复用 embedding_switch 双模型并存机制，重嵌完成才切换 |
| A1 OCR 依赖重、Mac 兼容性未知 | 先做无 OCR 的轻量步；OCR 作为 optional extra 装了才生效 |
| A2 改 chunks 语义，旧数据兼容风险 | parent_id 可空、无 parent 走原逻辑；评测集回归把关 |
| M6 本地小模型（gemma-4-e2b）工具调用可靠性不足 | 优先 OpenAI tools 协议 + 自动回退文本 ReAct；循环护栏（轮数/token 预算）；Agent 默认关、普通 RAG 不受影响；`--agent` 评测模式量化收益后再决定默认策略 |
| M6 联网搜索引入不可信内容 | Observation 标记为数据非指令；web_search 默认关闭需显式配置；read_url 继承 SSRF 防护与大小/超时限制 |
| M6 失控循环拖垮后端 | 最大轮数 + 全局 token 预算 + 单工具超时；SSE 可中断（复用现有 AbortController 停止链路） |
| 功能膨胀偏离单用户定位 | 每项实施前对照「明确不引入」清单；远期清单默认冻结 |

### 远期可选（本轮不做，仅记录方向）

- 数据源同步：Notion/飞书/RSS 定时拉取入库（个人工作流相关，做了很有感但工作量大）
- Telegram Bot 问答渠道（个人使用价值高、体量小）
- MCP 工具接入 / 知识图谱 / Wiki Mode / Langfuse——与单用户轻量定位冲突，不引入

### 明确不引入

LangChain/LangGraph 等 Agent 框架（ReAct 循环自研，约数百行，工具层接口化便于远期挂 MCP）、Go 双栈、Redis 任务队列、Neo4j、MinIO/S3、多向量库抽象层、RBAC/审计/用量统计、代码执行类高危工具。
