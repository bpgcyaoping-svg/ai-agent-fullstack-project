# login_demo 登录逻辑说明

本文结论全部来自对 `src/` 下源码的直接阅读，调用链逐层核对，未依赖文件名推测。

## 1. 服务启动与路由挂载

- `src/server.ts`：`createApp()` 创建 Express 应用，监听 `process.env.PORT ?? 3001`。
- `src/app.ts`：
  - `app.use(express.json())` —— 是 `request.body` 能取到值的前提。
  - `app.use("/api", loginRouter)` —— 登录路由被挂在 `/api` 前缀下。
  - `app.use("/api", profileRouter)` —— 受保护路由同样挂在 `/api` 下。
  - 另有 `GET /health` 返回 `{ status: "ok" }`。
- 因此登录接口的**完整路径是 `POST /api/login`**，受保护接口是 `GET /api/profile`。

## 2. 登录请求处理链路

`POST /api/login`

1. `src/routes/login.ts` 的 `loginRouter.post("/login", ...)` 处理入口。
2. 从 `request.body` 解构 `email`、`password`（类型标注为可选字符串）。
3. 任一为空 → 直接返回 `400 { error: "email and password are required" }`。
4. 否则调用 `authService.login(email, password)`。
5. 成功 → `200` 返回 Service 结果；抛错 → 统一 `401 { error: message }`。

### 2.1 Service 层：`src/services/auth-service.ts`

`AuthService.login(email, password)`：

1. `userRepository.findByEmail(email)` 查用户。
2. 查不到 → `throw new Error("User not found")`。
3. `verifyPassword(password, user.passwordHash)` 校验密码。
4. 不匹配 → `throw new Error("Invalid password")`。
5. 通过 → `issueAccessToken({ userId: user.id, role: user.role })` 签发令牌。
6. 返回 `LoginResult`，其中 `user` **只挑选** `id / email / displayName / role`，显式避免 `passwordHash` 泄漏到响应体。

### 2.2 Repository 层：`src/repositories/user-repository.ts`

- `findByEmail(email)` 仅调用 `queryOne("users", { email })`，不含业务判断。

### 2.3 数据访问层：`src/db/database.ts`

- 不是真实数据库，而是模块级内存数组 `usersTable`，进程重启即重置。
- `getTable(table)` 用穷尽式 `switch` 只支持 `"users"`。
- `queryOne(table, where)`：遍历行，要求 `where` 的**全部键**都严格相等（`===`），返回第一条命中记录，否则 `undefined`。
- 内置两条种子数据：
  - `u_1001 / demo@example.com / hash:demo123 / member`
  - `u_1002 / admin@example.com / hash:admin123 / admin`
- 注意：`queryOne` 的 `where` 是 `Partial<UserRecord>`，理论上可传入 `passwordHash` 等任意字段，只是当前调用方仅传 `email`。

### 2.4 密码校验：`src/utils/password.ts`

```ts
return passwordHash === `hash:${plainTextPassword}`;
```

- 源码注释已明确：这是 demo 级校验，**明文加 `hash:` 前缀做字符串比较，并非真实哈希**。

### 2.5 令牌签发：`src/utils/token.ts`

- `TOKEN_PREFIX = "demo-token."`
- `issueAccessToken(payload)`：`JSON.stringify(payload)` → `base64url` 编码 → 拼接为 `demo-token.<encoded>`。
- `parseAccessToken(token)`：
  - 前缀不是 `demo-token.` → `null`；
  - 解码 + `JSON.parse` 失败 → `catch` 返回 `null`；
  - 成功 → 返回 `AuthTokenPayload`（`{ userId, role }`）。
- 源码注释明确：**仅 base64url 编码，未加密、未签名，禁止用于生产**。

## 3. 登录后的鉴权链路

`GET /api/profile`（`src/routes/profile.ts` 挂了 `authMiddleware`）

1. `src/middleware/auth-middleware.ts` 读取 `request.headers.authorization`。
2. 缺失或不以 `"Bearer "` 开头 → `401 { error: "Missing bearer token" }`。
3. `parseAccessToken(...)` 返回 `null` → `401 { error: "Invalid token" }`。
4. 成功 → 写入 `response.locals.currentUser = payload`，调用 `next()`。
5. `profileRouter` 处理函数返回 `{ message: "Authenticated request", currentUser }`。

## 4. 错误码汇总

| 场景 | 位置 | 状态码 | 响应体 |
| --- | --- | --- | --- |
| 缺少 email/password | `routes/login.ts` | 400 | `"email and password are required"` |
| 用户不存在 | `services/auth-service.ts` | 401 | `"User not found"` |
| 密码错误 | `services/auth-service.ts` | 401 | `"Invalid password"` |
| 缺少 Bearer 头 | `middleware/auth-middleware.ts` | 401 | `"Missing bearer token"` |
| 令牌无法解析 | `middleware/auth-middleware.ts` | 401 | `"Invalid token"` |
| 请求体非合法 JSON | Express `json()` 中间件 | 400 | 由 Express 默认处理 |

## 5. 可被利用/需注意的行为细节（源码事实）

1. **用户枚举**：`User not found` 与 `Invalid password` 返回不同错误信息，客户端可据此判断某邮箱是否已注册。
2. **令牌可伪造**：`demo-token.<base64url>` 无签名，任何人都能用任意 `userId` / `role`（如 `admin`）自行构造并能通过 `authMiddleware`。
3. **令牌不校验真实性也不查库**：中间件只做解码与 JSON 解析，不校验用户是否仍存在、角色是否变更、是否过期；载荷中无过期字段。
4. **密码明文可比对**：`hash:` + 明文，任何拿到 `passwordHash` 的路径都等价于拿到明文口令。
5. **仅做非空校验**：`email`/`password` 只判断 falsy，未做类型与格式校验；非字符串入参（如对象）会传到 `findByEmail` 参与严格相等比较，必然不匹配。
6. **仓储层按 `email` 精确匹配**，大小写不做归一化，`Demo@Example.com` 无法登录。
7. **数据非持久化**：内存数组，重启丢失，且无并发/事务语义。

## 6. 简要结论

分层为 `server/app（装配）→ routes（HTTP 出入参）→ services（业务判断）→ repositories（按条件查询）→ db（内存表）`，横切能力由 `utils`（密码、令牌）与 `middleware`（鉴权）提供。登录成功返回 `{ accessToken, user }`，`user` 已脱敏；后续受保护请求依赖无签名的 demo 令牌。整体是教学演示实现，**不具备生产可用性**。

## 7. 实际引用过的源码路径

- `src/server.ts`
- `src/app.ts`
- `src/routes/login.ts`
- `src/routes/profile.ts`
- `src/services/auth-service.ts`
- `src/repositories/user-repository.ts`
- `src/db/database.ts`
- `src/types/user.ts`
- `src/utils/password.ts`
- `src/utils/token.ts`
- `src/middleware/auth-middleware.ts`
- `README.md`
