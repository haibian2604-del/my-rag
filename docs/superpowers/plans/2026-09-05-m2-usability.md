# M2 可用性实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 M2——多 workspace 前端管理、会话列表后端化、网页 URL 抓取入库（含 SSRF 防护）、嵌入模型切换向导（并行重嵌/回滚）、文档库状态汇总。验收：双 workspace 数据不串扰；换嵌入模型走向导流程可回滚（方案 §7 M2）。

**Architecture:** 在 M1 单体上扩展：URL 抓取作为新 source_type 走既有摄取管线；嵌入切换利用 chunk_embeddings 按 model_name 隔离的设计（新模型向量并行写入，切换/回滚只改 provider 配置指向）；会话列表补后端端点替换 localStorage。前端在现有三页上加工作区切换器、网页抓取入口、切换向导。

**Tech Stack:** 既有栈 + beautifulsoup4 / lxml（网页解析，M2 新增唯一运行时依赖）

**Spec:** `/Users/kk/code/project/my_rag/docs/02-实现方案.md`（§7 M2 行、§5 数据模型、§6 安全）

## Global Constraints

- 沿用 M1 全部约束：无 LangChain/Celery/Redis/openai SDK；providers/ 是唯一外部 AI 调用点；SSE；中文 UI 文案
- **新增运行时依赖只允许 beautifulsoup4、lxml**（解析网页正文用）
- SSRF 硬约束（方案 §6/§9 R9）：仅允许 http/https；解析后的目标 IP 属私有/环回/链路本地/组播段一律拒绝（`ipaddress` 标准库校验，且重定向每跳都验）；响应体 ≤10MB；超时 15s
- 嵌入切换硬约束（方案 §5）：向量按 `model_name` 并存，**绝不删除旧模型向量**；切换/回滚 = 改 embedding provider 的 model 字段；检索侧已按默认 provider model 过滤，无需改 search
- 摄取/重嵌任务失败必须落 documents 状态机或 app_config（embedding_switch），不静默
- 每个 Task 结束 commit；`uv run pytest` 全绿（当前 67 个不得回归）；`ruff check app/ tests/` 干净
- 共享开发库既定：测试用唯一名称前缀自清理，auth 残留由 conftest 兜底
- 注释/UI 中文，标识符英文

---

### Task 1: 会话列表后端化

**Files:**
- Create: `backend/tests/test_conversations_api.py`
- Modify: `backend/app/api/chat.py`（加 GET /api/workspaces/{ws_id}/conversations）、`frontend/src/pages/ChatPage.tsx`（删 localStorage 逻辑改调后端）、`frontend/src/api/client.ts`

**Interfaces:**
- Produces: `GET /api/workspaces/{ws_id}/conversations` → `[{id, workspace_id, title, created_at}]`（按 id 倒序）
- Frontend: 删除 `convListKey`/`loadConversations`/`saveConversations`；`conversations` 状态从后端拉取，新建/删除/改标题后 re-fetch；`ask` 成功后若标题为「新对话」需 re-fetch 列表（标题已被前端乐观更新，保持现有逻辑，只是持久层换成后端——注意：标题更新目前只写 localStorage，后端化后需要 `PUT /api/conversations/{id}` 改标题端点）

- [ ] Step 1: 写失败测试：创建会话→列表含之且倒序；PUT 改标题生效；跨 workspace 隔离
- [ ] Step 2: 实现 `GET /api/workspaces/{ws_id}/conversations` 与 `PUT /api/conversations/{id}`（body `{title}`，校验非空、≤200 字）
- [ ] Step 3: ChatPage 改造（删除 localStorage；打开页面/新建/删除/首次提问后刷新列表；标题更新调 PUT）
- [ ] Step 4: `uv run pytest` 全绿 + `pnpm build && pnpm vitest run` + commit

---

### Task 2: workspace 管理增强 + 前端切换器

**Files:**
- Create: `backend/tests/test_workspaces_api.py`（补持久化测试：200/201/409/PUT/DELETE）
- Modify: `backend/app/api/workspaces.py`（加 PUT/DELETE）、`frontend/src/App.tsx` + 新组件 `WorkspaceSwitcher.tsx`、`frontend/src/api/client.ts`

**Interfaces:**
- Produces: `PUT /api/workspaces/{id}` body `{name, description}`（重名 409）；`DELETE /api/workspaces/{id}`（级联删除文档/会话，204；至少保留一个工作区——删最后一个返回 409 `"至少保留一个工作区"`）
- Frontend: 侧栏底部工作区条 → 点击弹出切换器（列表 + 新建 + 删除 + 重命名）；切换后 `setWorkspace` 并使三页重新加载（现有 workspace.id 依赖的 effect 已处理）；新建默认名「工作区 N」
- App 状态提升：`workspace` 已在 App，无需全局 store

- [ ] Step 1: 失败测试（PUT 重名 409/成功；DELETE 后列表不含、再删最后一个 409；不存在的 404）
- [ ] Step 2: 实现 PUT/DELETE
- [ ] Step 3: WorkspaceSwitcher 组件（极简：下拉面板列出工作区，行内操作），App 挂接
- [ ] Step 4: 测试全绿 + commit

---

### Task 3: URL 抓取后端（SSRF 防护）

**Files:**
- Create: `backend/app/services/ingestion/web_fetch.py`、`backend/tests/test_web_fetch.py`
- Modify: `backend/pyproject.toml`（+beautifulsoup4、lxml）、`backend/app/api/documents.py`（POST /api/workspaces/{ws_id}/documents/url）

**Interfaces:**
- Produces:
  - `async def fetch_url_to_doc(ws_id: int, url: str) -> Document`：校验 URL → 抓取 → 提正文 → 存为 markdown 文档走既有管线（source_type="url"，filename=`域名_路径截断.md`，mime="text/markdown"）
  - `def validate_url(url: str) -> str`：仅 http/https；`ipaddress` 解析主机（域名先 `socket.getaddrinfo` 全部解析）拒绝 private/loopback/link-local/reserved/multicast；返回规范化 URL。**重定向每跳复验**（httpx `follow_redirects=False` 手动循环最多 5 跳）
  - 正文提取：BeautifulSoup + 简单启发（优先 `<article>`/`<main>`，退化 `<body>`），去 script/style/nav/footer，`get_text` 后空行分段——复用 parse_file 的 .txt 段落语义；标题 `<title>`/h1 作首行 `# 标题`
  - API：body `{"url": str}`；校验失败/抓取失败 400（detail 中文截断）；成功 201 返回 Document（pending，由后台任务摄取）
- 测试：validate_url 单测（http 拒绝、localhost/127.0.0.1/10.x/192.168.x/169.254.x 拒绝、正常公网域名过）；fetch 用 pytest-httpx mock（含重定向到私网被拒的用例）；API 测试 mock 抓取成功路径

- [ ] Step 1: 加依赖 + 写失败测试（SSRF 各段 + 重定向私网 + 正常路径）
- [ ] Step 2: 实现 validate_url + fetch + 正文提取 + API
- [ ] Step 3: 全绿 + commit

---

### Task 4: URL 抓取前端 UI

**Files:**
- Modify: `frontend/src/pages/DocumentsPage.tsx`

**Interfaces:**
- Produces: 拖放区下加「或添加网页」行：URL 输入框 + 「抓取」按钮；成功后刷新列表（新文档 filename 为页面标题）；400 错误显示红色提示（如「不允许访问内网地址」）

- [ ] Step 1: 实现（复用 client.post；抓取中 disabled 状态）
- [ ] Step 2: build/vitest/lint 全绿 + commit

---

### Task 5: 嵌入并行重嵌引擎

**Files:**
- Create: `backend/app/services/embedding_switch.py`、`backend/tests/test_embedding_switch.py`
- Modify: `backend/app/jobs/runner.py`（后台入口）

**Interfaces:**
- Produces:
  - `async def reembed_all(target_model: str) -> None`：遍历**全库所有 ready 文档的 chunk**，按 chunk 现有内容分批用「以 target_model 构造的默认 embedding provider」嵌入，写 `chunk_embeddings(model_name=target_model)`（先删该 model_name 的旧向量保证幂等）。进度写 app_config key=`embedding_switch`：`{"state": "running", "target_model", "total", "done"}`；完成→`"done"`；异常→`"failed"` + error。**不删其他 model_name 的向量**
  - 进度条更新粒度：每批（batch_size=32）commit 一次
  - `run_reembed_sync(target_model)`（runner，asyncio.run 包装）；并发防护：state=running 时拒绝再次启动
- 测试（fake embedding，dim 参数区分新旧模型）：造 2 文档若干 chunk + 旧模型向量 → reembed_all("new-model") → 断言新 model_name 向量齐全且旧向量仍在、进度 state=done 且 total/done 正确；重跑幂等；running 状态下再启动抛错

- [ ] Step 1: 失败测试
- [ ] Step 2: 实现（状态机 + 幂等 + 保留旧向量）
- [ ] Step 3: 全绿 + commit

---

### Task 6: 切换/回滚 API

**Files:**
- Create: `backend/app/api/embedding_switch.py`、`backend/tests/test_embedding_switch_api.py`
- Modify: `backend/app/main.py`（注册路由，挂 require_auth）

**Interfaces:**
- Produces:
  - `POST /api/settings/embedding/switch` body `{"target_model": str}` → 202；校验：与当前默认 embedding 模型不同、state≠running、target_model 非空；把 BackgroundTasks 排入 reembed_all（**不自动切换 provider**）
  - `GET /api/settings/embedding/switch` → `{state, target_model?, total?, done?, error?, current_model}`（前端轮询）
  - `POST /api/settings/embedding/activate` body `{"model": str}`：把默认 embedding provider 的 model 改为该值——**校验该 model_name 的向量已存在**（404 否则）；这就是「验证后切换」
  - `POST /api/settings/embedding/rollback` body `{"model": str}`：同 activate（语义别名，要求该 model 有向量）；前端向导用它回旧模型
  - activate/rollback 记录：app_config `embedding_switch.previous_model` 方便一键回滚

- [ ] Step 1: 失败测试（202/running 409/同模型 400/activate 无向量 404/有向量 200 且 provider.model 变更/rollback）
- [ ] Step 2: 实现
- [ ] Step 3: 全绿 + commit

---

### Task 7: 前端嵌入切换向导

**Files:**
- Create: `frontend/src/components/EmbeddingSwitcher.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`（嵌入 Tab 下方向导区）

**Interfaces:**
- Produces: 向导三步（同页完成）：①输入或从模型列表选新模型 → ②「开始重嵌」→ 轮询进度条（done/total）→ 完成后显示「验证并切换」按钮（activate）→ ③切换后显示当前模型 + 「回滚到 xxx」（调 rollback）。失败显示 error + 重试
- 轮询 1.5s，state=done/idle 停止

- [ ] Step 1: 实现组件 + 挂入设置页嵌入 Tab
- [ ] Step 2: build/vitest/lint 全绿 + commit

---

### Task 8: 文档库汇总 + M2 验收回归

**Files:**
- Modify: `frontend/src/components/DocumentStatusList.tsx`（顶部汇总条：N 可问答 · N 处理中 · N 失败）、`backend/app/api/documents.py`（GET 列表响应不变——汇总由前端统计即可，**不加后端端点**，YAGNI）、`README.md`（M2 勾选 + 会话/工作区说明）

**验收（手工 + 脚本）：**
1. 双 workspace：A 传文档、B 不传 → 在 B 提问不命中 A 的内容（回归 test_retrieval 的隔离用例即可覆盖后端；前端切 workspace 验证列表切换）
2. 嵌入切换向导：当前 oMLX 模型 → 重嵌到新模型名（可用同模型别名演练）→ activate → 检索仍正常（评测脚本 `uv run python -m tests.evaluation.run_eval` recall@5 不降）→ rollback → 再跑评测
3. URL 抓取：抓一个公网页面入库可问答；抓 `http://localhost:xxxx` 被拒绝
4. `uv run pytest` 全绿、前端三件套绿、`docker compose build` 成功

- [ ] Step 1: 汇总条
- [ ] Step 2: 验收清单逐项执行并记录
- [ ] Step 3: README 更新 + commit
