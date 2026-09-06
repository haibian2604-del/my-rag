# 方案：登录注册页面（账号密码）

日期：2026-09-05 · 分支：feature/account-auth

## 背景

现有认证为「访问密码」单开关（AppConfig key=auth + JWT Cookie，本机免密）。
用户要求：补充登录注册页面，暂时只允许账号密码登录。

## 决策（Rulings）

- R1：账号制取代访问密码机制。users 表 + JWT Cookie（sub=username）。AppConfig 的 auth 开关、
  设置页「访问密码」区块移除——两种认证并存只会造成混乱。
- R2：注册仅在 users 表为空（未初始化）时允许，首个账号即管理员；之后注册接口返回 403。
  理由：本项目定位单用户自托管，开放注册等于把全部工作区数据暴露给任何能访问站点的人。
- R3：不再有「本机免密」默认态。users 为空时所有业务 API 返回 401（未初始化），前端展示注册页。
  一次性成本：部署后需先创建管理员账号。
- R4：登录失败不区分「用户不存在/密码错误」（统一提示，防用户名枚举）；不做登录限流（单机内网场景）。

## 后端

### 数据模型
新表 `users`：id PK, username String(50) unique, password_hash String(100)（bcrypt），created_at。
Alembic 迁移（down_revision=20260904_1000-a9f3c1d20e44）。

### core/auth.py 重构
- `get_auth_config/set_auth_config/verify_password(旧语义)` 移除；保留 bcrypt 哈希工具。
- `create_token(username)`：sub=username，TTL 30 天。
- `require_auth`：解析 Cookie JWT，校验签名与过期；无 Cookie/无效 → 401。不再读 AppConfig。

### api/auth.py
- `GET /api/auth/status`（免认证）：`{"registered": bool}`。
- `POST /api/auth/register`：仅 users 为空时可用（403 otherwise）。入参 username+password
  （username 1-50 字符 [A-Za-z0-9_-]，密码 ≥ 6 位）。成功即种 Cookie。
- `POST /api/auth/login`：username+password，成功种 Cookie；失败 401「用户名或密码错误」。
- `POST /api/auth/logout`：删除 Cookie（保持现有）。
- `PUT /api/auth/password`：需认证，body {old_password, new_password}，校验旧密码后更新。

### conftest
`_reset_auth_state` 改为：清空 users 表（注册类测试各自建号）。

### 测试
tests/test_auth.py 重写：
- 未注册时 status.registered=False、业务 API 401、注册成功并种 Cookie
- 注册后再注册 403；登录成功/失败；错误旧密码改密失败；正确改密后旧密码登录失败新密码成功
- 未登录访问受保护 API 401（401 事件链已有测试沿用）

## 前端

- `LoginPage.tsx`（新页面，取代 LoginGate）：挂载时 GET /api/auth/status；
  registered=false 显示「创建管理员账号」（username+password+确认密码），
  true 显示登录表单；成功后回调进入应用。
- App.tsx：LoginGate 换为 LoginPage；401 事件同样回到该页。
- api/client.ts：无需改动（post/put 已有；status 用 get）。
- SettingsPage：移除「访问密码」区块，新增「修改密码」区块（旧密码+新密码）。
- README 认证描述更新。

## 任务拆分

- T1：后端全部（模型/迁移/auth 重构/路由/conftest/测试）。
- T2：前端全部（LoginPage/App/Settings/README）。

## 全局约束

- 不引入 openai SDK 之外的新第三方依赖（bcrypt/jwt 已有；前端零新依赖）。
- 全部 API 前缀 /api；错误 detail 用中文。
- 测试完测完即关：只关闭自己启动的进程；用户自启进程（8001 的 python -m app.main、5173 dev server）不动。
- 提交信息中文，沿用现有风格。
