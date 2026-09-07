# MCP 服务设计方案：让外部 Agent 连接知识库（待确认）

日期：2026-09-07 · 分支（实施时）：m7-mcp · 前置：M6 已完成（main a4ecee5）

## 一、目标与定位

把「知笥」知识库以 **MCP（Model Context Protocol）服务器** 形态暴露，任何支持 MCP 的客户端（Claude Desktop / Claude Code / 其他 Agent 框架）即可检索与问答本知识库。个人自托管定位不变：**单用户、读优先、复用现有 FastAPI 进程与认证体系**。

## 二、关键技术决策（先给定论）

| 决策点 | 结论 | 理由 |
|---|---|---|
| SDK 选型 | **`fastmcp`（gofastmcp 2.x，PrefectHQ）** | 自带 `combine_lifespans` 工具，挂载到现有 FastAPI 的 lifespan 共处问题是官方推荐姿势直接解决；认证/挂载人体工学最好；维护活跃。官方 `mcp` v2 包无此工具（为它手写 lifespan 合并不如直接用 fastmcp） |
| 传输方式 | **Streamable HTTP**，`mcp.http_app(path="/mcp")` 生成子 ASGI 挂载到现有 FastAPI | 单进程单部署（dev 8001 / compose 9000），共享 DB 配置；stdio 模式仅本地 CLI 有用，本期不做（远期可加薄包装） |
| 认证 | **API Key（Bearer）**，新增 api_keys 表；设置页管理 | MCP 客户端拿不到 JWT Cookie（登录是浏览器流程）；静态 Bearer Key 是 MCP 客户端（自定义 header）最通用的方式；个人自托管无需 OAuth 全套 |
| 工具范围 | **只读五工具**（search / ask / list_workspaces / list_documents / get_document） | 外部 Agent 的核心诉求是检索与问答；写操作（入库）涉及磁盘与解析管线，暴露给外部工具的风险面大，本期不做（远期可加 `ingest_url` 且默认关） |
| 鉴权粒度 | Key = 全工作区只读访问权；工具内可按 `workspace_id` 收窄 | 单用户下分 key 分权是过度设计；key 仅用于「谁在连」与吊销 |

## 三、架构设计

```
外部 MCP 客户端（Claude Code / 其他 Agent）
   │  POST /mcp  (Streamable HTTP, Authorization: Bearer zk-xxx)
   ▼
FastAPI 主应用（现有进程，lifespan 经 combine_lifespans 合并主应用与 MCP 两段）
   ├─ ASGI 中间件（仅作用于 /mcp 挂载）：校验 Bearer key
   │    api_keys.key_hash bcrypt 比对 → 通过则注入 key 上下文（last_used_at 更新）
   │    失败 → 401
   ├─ app.mount("/mcp", mcp.http_app(path="/mcp"))  # fastmcp 生成的 Starlette 子应用
   │
   └─ backend/app/services/mcp_server.py（FastMCP 实例 + 工具定义）
        ├─ list_workspaces()                     → [{id, name, description}]
        ├─ search(query, workspace_id?, top_k=5) → retrieve() 命中（filename/heading_path/page_no/content 摘要）
        ├─ ask(question, workspace_id?)          → 完整 RAG 回答（build_context + LLM 非流式生成）+ citations 结构化返回
        ├─ list_documents(workspace_id, status?) → [{id, filename, status, summary}]
        └─ get_document(doc_id)                  → 详情 + preview 前 500 字
```

要点：
- **lifespan 共处用 `combine_lifespans` 解决**（fastmcp.utilities.lifespan，官方为此场景提供的工具）：create_app 的 lifespan 改为 `combine_lifespans(main_lifespan, mcp_http_app.lifespan)`——主应用现有的 recover_interrupted 等启动逻辑与 MCP 会话管理器（StreamableHTTPSessionManager）在同一 ASGI lifespan 内先后进入/退出，互不抢夺；不再有「挂载后 MCP 会话管理器未初始化 / lifespan 被主应用吞掉」的集成坑。
- **工具实现全部复用既有服务层**（retrieve / build_context / llm_complete / providers_service），MCP 层只做参数校验与结果整形——不复制业务逻辑。
- `ask` 走非流式（MCP 工具调用天然一问一答），复用 M5 的 `llm_complete`；LLM 未配置时返回明确的工具错误文案（不 500）。
- `search`/`ask` 的 `workspace_id` 可选：缺省时只有一个工作区则自动选中，多工作区则要求显式指定（返回可读错误 + 工作区列表提示）。

## 四、数据模型（api_keys）

```
api_keys: id PK, name String(100)（用途备注，如 "claude-code"）,
          key_prefix String(12)（明文前缀，如 zk-1a2b，用于列表识别）,
          key_hash String(100)（bcrypt）,
          created_at, last_used_at（可空）
```
- 生成规则：`zk-` + secrets.token_urlsafe(24)；明文只在创建响应返回一次。
- 迁移：单表 create，downgrade drop。

## 五、API（设置页用，非 MCP）

- `POST /api/keys {name}` → 201 {id, name, key_prefix, key}（明文仅此一次）
- `GET /api/keys` → [{id, name, key_prefix, created_at, last_used_at}]
- `DELETE /api/keys/{id}` → 204
- 全部挂 require_auth（浏览器 Cookie）。

## 六、前端（设置页新增「API 密钥」区块）

- 与「修改密码」同区布局：名称输入 + 创建按钮 → 弹层展示明文 key 一次（复制按钮 + 「关闭后无法再查看」提示）；列表显示 name/prefix/创建时间/最近使用；吊销按钮（确认）。

## 七、客户端接入（README 文档化）

```
Claude Code / 其他 MCP 客户端配置示例：
{
  "mcpServers": {
    "zhifu": {
      "type": "http",
      "url": "http://<host>:8001/mcp",
      "headers": { "Authorization": "Bearer zk-xxxx" }
    }
  }
}
```
README 补：工具清单表、生成 key 步骤、compose 部署的端口注意事项（9000）。

## 八、测试策略

- api_keys：创建（明文一次性）/列表/吊销后 401/bcrypt 存储（库里无明文）。
- MCP 层：官方 mcp 客户端 + ASGI 内存传输（httpx ASGITransport 或 SDK memory transport）做端到端工具调用——list_workspaces、search 命中（fake embedding 种子数据）、ask（TestModel/llm mock）、无 key 401、坏 key 401、吊销后 401。
- 安全：key 不出现在日志断言；bcrypt hash 而非明文/可逆加密。
- 基线 217 passed / 1 skipped 持续全绿。

## 九、任务拆分（实施时走 superpowers SDD，约 2~2.5 天）

- **T1 API Key 后端**：模型/迁移/三个 settings API/测试（约 0.5 天）
- **T2 MCP 服务器**：fastmcp 依赖引入、mcp_server.py 五工具、`http_app(path="/mcp")` 挂载 + **combine_lifespans 接线** + Bearer 中间件、MCP 客户端内存端到端测试（约 1 天）
- **T3 设置页 UI + README**（约 0.5 天）
- **收尾**：真实客户端联调（控制器用临时端口 + MCP 客户端跑一次握手与工具调用，重点验证 lifespan 合并后会话初始化正常）、全量回归、最终评审

## 十、风险

| 风险 | 缓解 |
|---|---|
| ~~FastMCP 挂载与主应用 lifespan 冲突~~ | **已解决**：采用 `combine_lifespans`（fastmcp 官方为此场景提供的工具）把主应用 lifespan 与 MCP http_app 的 lifespan 合并为单一 ASGI lifespan；实现后仍以真实客户端握手验证一次 |
| fastmcp 依赖较重/版本迭代快 | 锁定版本；只用到 FastMCP 实例/工具注册/http_app/combine_lifespans 四个稳定面，不追新特性 |
| MCP 端点被未授权扫描 | Bearer 强制 401；工具只读；compose 部署文档提示不要裸露公网 |
| ask 工具被外部 Agent 高频调用拖累 LLM | 个人使用面小；工具内无队列，远期可加每 key 限流（本期不做，记录） |
