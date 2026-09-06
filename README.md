# 知笥 · 个人知识库

自托管的中文优先个人知识库：上传文档（PDF / Word / Markdown / TXT），自动解析、切分并向量化，然后对话式问答——回答基于你的文档生成，并附带可溯源的引用标记。

> 命名取自「笥」（sì）：古时藏书之箧。设计语言为纸、墨、钤印：回答以衬线正文排版，引用以印泥红的「笺注」挂在答案下方。

## 功能

- **文档摄取**：上传 md / txt / pdf / docx，或直接粘贴网页 URL 抓取入库（内置 SSRF 防护，拒绝内网地址）；后台任务解析 → 结构感知切分（保留标题路径、页码）→ 嵌入 → pgvector 入库；状态机（排队/解析/向量化/可问答/失败）全程可见，列表顶部有可问答/处理中/失败汇总，服务重启自动恢复
- **检索问答**：混合检索（向量召回 + jieba 分词全文检索，RRF 融合，可按工作区关闭）→ 可选重排（oMLX reranker 等 Jina 兼容端点）→ 流式回答（SSE）→ 引用角标溯源（文件名 / 章节路径 / 页码 / 原文片段）；多会话按工作区组织，历史对话可续聊
- **模型可插拔**：任何 OpenAI 兼容端点均可（[oMLX](https://omlx.ai/) / Ollama / vLLM / 云 API）；设置页支持连接测试与模型自动发现，密钥加密存储；嵌入模型切换向导支持并行重嵌新模型、一键激活与回滚，切换期间检索不受影响
- **多工作区**：不同知识域相互隔离，删除工作区时其文档磁盘文件一并回收；工作区级检索参数（Top K / 相似度阈值 / 上下文上限 / 混合检索 / 重排）
- **可选密码**：默认本机免密，可在设置页开启访问密码（JWT Cookie）
- **数据备份**：`scripts/backup.sh` 一键导出数据库与文档快照，`scripts/restore.sh` 配合完成恢复（用法见 `scripts/README.md`）

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

## 混合检索说明

存量文档升级后需回填全文索引（新文档自动写入）：

```bash
cd backend && uv run python -m app.services.ingestion.backfill_fts
```

评测脚本可对比混合检索与纯向量基线：

```bash
cd backend && uv run python -m tests.evaluation.run_eval --compare
```
