# 知笥 · 个人知识库

自托管的中文优先个人知识库：上传文档（PDF / Word / Markdown / TXT），自动解析、切分并向量化，然后对话式问答——回答基于你的文档生成，并附带可溯源的引用标记。

> 命名取自「笥」（sì）：古时藏书之箧。设计语言为纸、墨、钤印：回答以衬线正文排版，引用以印泥红的「笺注」挂在答案下方。

## 功能

- **文档摄取**：上传 md / txt / pdf / docx，或直接粘贴网页 URL 抓取入库（内置 SSRF 防护，拒绝内网地址）；后台任务解析 → 结构感知切分（保留标题路径、页码）→ 嵌入 → pgvector 入库；状态机（排队/解析/向量化/可问答/失败）全程可见，列表顶部有可问答/处理中/失败汇总，服务重启自动恢复
- **检索问答**：混合检索（向量召回 + jieba 分词全文检索，RRF 融合，可按工作区关闭）→ 可选重排（oMLX reranker 等 Jina 兼容端点）→ 流式回答（SSE）→ 引用角标溯源（文件名 / 章节路径 / 页码 / 原文片段）；多会话按工作区组织，历史对话可续聊
- **Agent 模式（实验性）**：会话级「Agent」开关（输入框旁，后端启动时探测默认 LLM 支持工具调用才显示）；开启后由 Agent 自主调用工具作答——`kb_search` 检索当前工作区知识库、`read_url` 抓取网页正文（内置 SSRF 防护；`web_search` 搜索未实装），回答气泡内嵌可折叠的工具调用时间线并随历史消息保存；护栏：工具调用轮数上限、工具结果截断、整体生成 120s 超时，超限/超时/失败一律降级为中文提示收尾，绝不挂死连接
- **模型可插拔**：任何 OpenAI 兼容端点均可（[oMLX](https://omlx.ai/) / Ollama / vLLM / 云 API）；设置页支持连接测试与模型自动发现，密钥加密存储；嵌入模型切换向导支持并行重嵌新模型、一键激活与回滚，切换期间检索不受影响
- **多工作区**：不同知识域相互隔离，删除工作区时其文档磁盘文件一并回收；工作区级检索参数（Top K / 相似度阈值 / 上下文上限 / 混合检索 / 重排）
- **账号登录**：默认无账号时首次访问需创建管理员账号；之后账号密码登录，凭据以 JWT Cookie 保存
- **数据备份**：`scripts/backup.sh` 一键导出数据库与文档快照，`scripts/restore.sh` 配合完成恢复（用法见 `scripts/README.md`）
- **MCP 服务**：外部 Agent（Claude Code、Cursor 等）经 Streamable HTTP + API Key 连接知识库（详见下方「MCP 服务」）

## MCP 服务

内置 MCP（Model Context Protocol）服务器，端点 `http://<host>:8001/mcp`（Streamable HTTP），使用 Bearer API Key 认证。所有工具均为只读，不会修改知识库内容。

### 工具清单

| 工具 | 参数 | 说明 |
|---|---|---|
| `list_workspaces` | 无 | 列出全部工作区（id / 名称 / 描述） |
| `search` | `query`，`workspace_id?`，`top_k?`（默认 5） | 混合检索知识库，返回命中段落与引用信息 |
| `ask` | `question`，`workspace_id?` | 基于知识库生成带引用的回答 |
| `list_documents` | `workspace_id`，`status?` | 列出工作区内的文档（可按状态过滤） |
| `get_document` | `doc_id` | 查看单个文档的详情（元信息与内容） |

> 有多个工作区时，`search` / `ask` 必须显式传入 `workspace_id`（可先用 `list_workspaces` 查询）；只有单个工作区时可省略。

### 生成 API Key

在 Web 界面「设置 → API 密钥」中创建：输入名称点击「创建密钥」，明文密钥（`zk-` 开头）只展示一次，请立即复制保存；之后可在列表中随时吊销。

### 客户端配置

以 Claude Code 为例，在 MCP 配置中加入：

```json
{
  "mcpServers": {
    "zhifu": {
      "type": "http",
      "url": "http://<host>:8001/mcp",
      "headers": { "Authorization": "Bearer zk-xxxxxxxx" }
    }
  }
}
```

Docker Compose 部署时应用经 `http://<host>:9000` 对外暴露，MCP 端点即 `http://<host>:9000/mcp`（容器内同一服务，无需额外映射端口）。API Key 等同于知识库完整读取权限，请勿将 9000 端口或密钥暴露到公网。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic |
| 存储 | PostgreSQL 16 + pgvector（业务数据、向量、任务状态单库承载） |
| 前端 | React 19 · Vite · TypeScript · Tailwind CSS 4 |
| 文档解析 | PyMuPDF · python-docx · markdown-it-py |
| 模型接入 | httpx 直连 OpenAI 兼容协议（不依赖 openai SDK / LangChain） |

架构原则：单服务 + 单库，无 Redis / Celery / 独立向量库；`providers/` 是唯一出现外部 AI 调用的模块；所有后台任务有状态机与启动恢复。设计取舍详见 [docs/02-实现方案.md](docs/02-实现方案.md)（竞品分析见 [docs/01-竞品分析.md](docs/01-竞品分析.md)）。

## 本地开发

依赖：Python 3.12（[uv](https://docs.astral.sh/uv/)）、Node ≥ 20（pnpm）、PostgreSQL 16 + pgvector。

```bash
# 1. 数据库（已有 pgvector 实例可跳过）
docker run -d --name pgvector -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=123456 -p 5432:5432 pgvector/pgvector:pg16
docker exec pgvector psql -U postgres -c "CREATE DATABASE rag;"
docker exec pgvector psql -U postgres -d rag -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 2. 后端（默认连 postgresql://postgres:...@localhost:5432/rag，
#    可用 backend/.env 覆盖：RAG_DATABASE_URL=postgresql+psycopg://...）
cd backend && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --port 8001

# 3. 前端（dev 代理 /api → localhost:8001）
cd frontend && pnpm install && pnpm dev
```

打开 http://localhost:5173 ，在「设置」页配置模型服务地址与模型名，测试连接通过后上传文档即可问答。

### 测试与评测

```bash
cd backend && uv run pytest -v      # 67 个用例（含真实库集成测试）
cd frontend && pnpm vitest run

# 中文检索评测（20 条查询，输出 recall@5 与延迟；需已配置嵌入模型）
cd backend && uv run python -m tests.evaluation.run_eval
```

## 部署（Docker Compose）

```bash
docker compose up -d --build
```

应用在 `http://localhost:9000`，首次启动自动执行数据库迁移。模型服务在宿主机运行时（如 oMLX 的 `http://localhost:8000/v1`），在设置页把地址写作 `http://host.docker.internal:8000/v1`。

暴露到局域网前请覆盖密钥环境变量：

```bash
RAG_JWT_SECRET=$(openssl rand -hex 32) \
RAG_ENCRYPTION_KEY=$(openssl rand -hex 32) \
docker compose up -d --build
```

## Roadmap

- [x] Docker Compose 一键部署（含 SPA 托管）
- [x] 中文检索评测集与 recall@5 脚本
- [x] M2：网页 URL 抓取入库、嵌入模型切换向导（并行重嵌 / 回滚）
- [x] M3：重排接入默认链路（oMLX reranker）、混合检索（jieba + FTS + RRF）、数据备份脚本
- [x] M4：HNSW 向量索引、检索阶段进度（SSE stage）、父子分块（子块检索 / 父块上下文）、PDF 表格转 Markdown 与扫描件识别
- [x] M5：管理与体验增强（建议问题与追问、文档自动摘要与预览、e2e 答案质量评测、FAQ 知识库模式）
- [x] M6：ReAct Agent 问答模式（PydanticAI；会话级开关、kb_search / read_url 工具、轮数 / 截断 / 超时护栏、capability 探测）
- [x] MCP 服务：外部 Agent 经 Streamable HTTP + API Key 连接知识库（fastmcp，五个只读工具）

## 混合检索说明

存量文档升级后需回填全文索引（新文档自动写入）：

```bash
cd backend && uv run python -m app.services.ingestion.backfill_fts
```

评测脚本可对比混合检索与纯向量基线：

```bash
cd backend && uv run python -m tests.evaluation.run_eval --compare
```

## 父子分块说明

新文档默认按「父块 500 token / 子块 200 token（同节内轻重叠）」父子切分：嵌入与全文索引都建在子块上，检索命中子块后按父块聚合去重，引用返回父块全文作为更完整的上下文。存量文档不回填也不影响检索（旧块自身即检索单元）；回填后可享受父块级上下文：

```bash
cd backend && uv run python -m app.services.ingestion.backfill_parent_child
```

## FAQ 知识库模式

上传的 Markdown 文件若首部含 `<!-- faq -->` 标记，即按 FAQ 结构解析。支持三种问答形态（可混用）：

```markdown
<!-- faq -->

Q: 如何重置密码？
A: 在登录页点击「忘记密码」，按邮件指引操作。

**问**：支持哪些付款方式？
**答**：支付宝、微信与银行卡。

| 问题 | 答案 |
| --- | --- |
| 退货流程是什么？ | 在订单页申请退货，寄回后退款。 |
```

每个问答对切成一个独立父块（全文 = 问题 + 答案），问题部分作为子块承担向量与全文检索；检索命中后引用返回完整问答父块，标题路径显示为 `FAQ: {问题前 30 字}`，问答不会跨对被切断或混排。文件带标记但解析不出任何问答对时，自动降级为普通 Markdown 解析，不影响正常入库。
