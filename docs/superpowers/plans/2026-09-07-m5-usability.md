# M5 实施计划：管理与体验增强（B1/B2/B4/B3）

来源：docs/03-WeKnora对标优化方案.md（已确认）· 分支：m5-usability · 2026-09-07

## 全局约束

- 不新增第三方依赖；ROUGE-L 自实现（公式简单，收敛标准：F1 值 0-1）。
- 错误 detail 与注释中文；检索/问答永不因新特性失败（建议问题/摘要/追问生成失败均降级为「不显示」）。
- 现有 160 passed / 1 skipped 基线持续全绿；每任务独立提交（中文信息）。
- 测试进程测完即关（只关自己启动的）；用户进程（8001/5173）不动。
- LLM 生成类能力统一走既有 get_default_provider("llm") + OpenAICompatLLM 流式/非流式接口；无 LLM 配置时全部特性静默关闭（前端不显示入口或返回空）。

## T1（B1）建议问题与追问

后端：
- 新增 `GET /api/workspaces/{ws_id}/suggestions`：工作区文档为空 → `{"questions": []}`；否则取该工作区前 N 个 ready 文档的父块片段（复用检索 SQL 简化版：随机/序取 3-5 块），拼入固定 prompt 调 LLM（非流式，OpenAICompatLLM 或直接 httpx）生成 3 个建议问题，JSON 数组解析（容错：解析失败/超时 8s/无 LLM → 空）。响应缓存 app_config key=`suggestions:{ws_id}`（含 doc 数量指纹，文档变化即失效重算）。
- chat.py 的 ask SSE `done` 事件扩展为 `{"type":"done","followups":[...]}`：回答完成后异步生成（不阻塞流结束——实现为生成完回答后、yield done 前同步调用一次 LLM，prompt = 用户问题 + 回答摘要 → 3 个追问；失败/无 LLM → followups: []。若耗时不可接受改为 done 后前端不再展示，允许实现者实测后简化为「done 不带 followups」的降级方案，但需在报告说明）。
前端：
- ChatPage 会话空态（无消息时）显示建议问题按钮（点击即填入输入框发送）；每次回答结束后在回答下方展示追问 chips（点击即发问）。建议问题加载失败静默不显示。

测试：suggestions 接口（无文档空、有文档 mock LLM 返回、LLM 失败降级空、缓存指纹）；SSE done 事件 followups 断言（mock LLM）。

## T2（B2）文档自动摘要与预览

后端：
- documents 表加 `summary TEXT` 列（Alembic 迁移，downgrade drop）。
- ingest_document 成功（status=ready）后触发摘要生成：取文档前 ~2000 字符 + 标题路径结构，LLM 非流式生成 ≤100 字中文摘要，写 documents.summary；失败/无 LLM → summary 保持 NULL（不阻塞摄取状态机，摘要生成放在状态置 ready 之后独立 try）。
- 新增 `POST /api/documents/{doc_id}/summary`：手动（重新）生成摘要，同样失败降级。
- `GET /api/documents/{doc_id}`（已有详情接口）补 preview 字段：读原文文件前 500 字符（复用 doc_file_path）。
- 列表接口 DocumentOut 增加 summary 字段。
前端：DocumentsPage 文档行/详情显示摘要（有则显示，无则不显示位）；详情弹层或展开区显示 preview 前几行。
测试：迁移、摄取后摘要生成（mock LLM）、摘要失败不影响 ready、手动接口、preview 字段。

## T3（B4）答案质量评测（e2e）

- run_eval.py 增加 `--e2e` 模式：在 recall 检索基础上，真实调用 LLM（读默认 llm provider 配置；未配置直接退出提示）生成回答，计算：
  - 关键词覆盖率（expect_keywords 在回答中命中率，沿用现有 OR 语义）；
  - ROUGE-L F1（回答 vs qa_set 新增字段 `reference`（参考答案），自实现 LCS，~30 行；qa_set.jsonl 补 reference 字段——为现有 22 条各写一句参考答案）。
- 输出逐条 ✓/✗ + 汇总（关键词覆盖率 %、平均 ROUGE-L）。`--e2e` 与 `--compare` 互斥时可并存（e2e 只对 hybrid 路跑）。
- 单测：ROUGE-L 函数（已知句子对、边界：空串/完全相同/无重叠）；e2e 主流程用 mock LLM 测。

## T4（B3，方案标可选，纳入本里程碑）FAQ 知识库模式

- 约定格式：上传文档文件名以 `faq` 前缀或内容含 `<!-- faq -->` 标记时（实现者二选一并写文档说明；倾向内容标记，文件名约定太隐晦），解析器把 `Q:`/`A:`（或 `**问**`/`**答**`，markdown 表格两列）解析为结构化 FAQ 对。
- 摄取：每个 FAQ 对 = 一个独立父块（question+answer 合并全文），其 question 部分为子块（embedding/FTS 建在子块），answer 在父块全文中——复用 T3(M4) 父子结构，无新表。
- 检索无需改动（叶子原则自动生效）；引用 heading_path 显示 `FAQ: {question前30字}`。
- 测试：FAQ 文档解析（Q/A 对数量与内容）、摄取后父子结构正确、检索命中 FAQ 返回完整问答父块。
- 实现者若评估后认为解析格式歧义大，可缩减为仅支持「markdown 表格两列 | 问题 | 答案 |」单一格式，报告说明即可。

## 顺序与依赖

T1 → T2 → T3 → T4。T1/T2 都新增「LLM 非流式调用」小工具（建议放 services/chat/llm_util.py 或复用现有 OpenAICompatLLM 的非流式封装），T2 实现时以 T1 产出为准复用；T3 独立；T4 只触解析/摄取。
