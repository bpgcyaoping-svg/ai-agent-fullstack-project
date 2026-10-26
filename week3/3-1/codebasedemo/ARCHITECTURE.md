# codebasedemo 代码分层架构说明

> 面向「代码理解 Agent」的最小可运行分层示例：模型接入、执行上下文、工具运行时、pi 工具适配、循环保护、装配层各自独立成层，由装配层接到 pi 原生 Agent Loop 上。
> 本文档只描述**当前仓库既有实现**，不包含改造建议。

---

## 1. 项目定位

一次 Run 的任务是固定的：**分析 `login_demo` 仓库的登录逻辑，读够源码后生成 `artifacts/login-flow.md`**。

核心设计原则：

| 原则 | 含义 | 体现位置 |
| --- | --- | --- |
| 不自建循环 | 循环完全交给 pi 的 `runAgentLoop` | [src/agent-runner.ts](src/agent-runner.ts) |
| 单一执行入口 | 所有工具能力只能经由 `DemoToolRuntime.invoke` | [src/runtime.ts](src/runtime.ts) |
| 层间单向依赖 | 上层依赖下层，下层不反向引用上层 | 见第 5 节 |
| 停止原因唯一 | 一旦设定不得被后续覆盖 | [src/loop-guard.ts](src/loop-guard.ts) |
| 密钥不落代码 | 只从环境变量读取 | [src/model.ts](src/model.ts) |

---

## 2. 目录结构

```
codebasedemo/
├── src/
│   ├── main.ts          # CLI 入口：启动一次 Run 并打印结果
│   ├── agent-runner.ts  # 装配层：pi Agent Context / 钩子 / 事件观察
│   ├── model.ts         # 模型接入层：逻辑模型 + Gateway Provider + 流式入口
│   ├── run-context.ts   # 执行上下文：项目根 / 仓库根 / 产物根 / 交付物路径
│   ├── runtime.ts       # 统一工具 Runtime：四种能力 + 治理 + 错误归一
│   ├── pi-tools.ts      # pi 工具适配层：Schema → Runtime → pi 结果
│   └── loop-guard.ts    # 停止决策中心：重复动作 / 最大轮数 / 证据校验
├── tests/
│   ├── support/harness.ts   # 隔离的临时工程根 + 真实 login_demo 注入
│   ├── runtime.test.ts      # Runtime 层验收
│   ├── pi-tools.test.ts     # 适配层验收
│   ├── loop-guard.test.ts   # 停止决策层验收
│   ├── loop.test.ts         # 装配层端到端场景
│   └── model.test.ts        # 模型接入层验收
├── artifacts/login-flow.md  # Agent 产出物（唯一可写目录）
├── package.json / tsconfig.json / vitest.config.ts
```

---

## 3. 分层视图

```
┌─────────────────────────────────────────────────────────────┐
│  CLI 入口          main.ts                                   │
│  只负责：发起 Run、把文本增量写 stdout、打印结果摘要          │
└───────────────────────────┬─────────────────────────────────┘
                            │ 注入 model / streamFn / signal / onText
┌───────────────────────────▼─────────────────────────────────┐
│  装配层            agent-runner.ts                           │
│  System Prompt、Agent Context、pi 钩子映射、事件观察、Trace   │
└──────┬──────────────┬──────────────┬──────────────┬──────────┘
       │              │              │              │
       │ 模型          │ 工具          │ 停止判定      │ 上下文
┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌────▼─────────┐
│ model.ts    │ │ pi-tools.ts│ │loop-guard  │ │run-context.ts│
│ 模型接入层   │ │ 工具适配层  │ │ 停止决策层  │ │ 执行上下文    │
└──────┬──────┘ └─────┬──────┘ └─────┬──────┘ └──────────────┘
       │              │              │ 只读工具名常量
       │ 流式请求      │ 统一调用      │
┌──────▼──────────────▼──────────────▼─────────────────────────┐
│  pi 原生 Agent Loop（runAgentLoop，第三方）                   │
└──────────────────────┬───────────────────────────────────────┘
                       │ 唯一工具执行入口
┌──────────────────────▼───────────────────────────────────────┐
│  统一 Tool Runtime     runtime.ts                             │
│  未注册→参数校验→路径边界→执行→结果治理                        │
└──────────────────────┬───────────────────────────────────────┘
                       │ fs/promises
             ┌─────────▼──────────┐        ┌──────────────────┐
             │ login_demo（只读）  │        │ artifacts（只写） │
             └────────────────────┘        └──────────────────┘
```

依赖方向自上而下：`main → agent-runner → {model, pi-tools, loop-guard, run-context}`，`pi-tools → runtime → run-context`。

---

## 4. 各层职责与边界

### 4.1 执行上下文 `run-context.ts`

一次 Run 的**路径边界唯一来源**，纯函数、无副作用、不碰文件系统。

| 字段 | 默认值 | 用途 |
| --- | --- | --- |
| `projectRoot` | 由模块自身位置推导（`src/..`） | 写入越界校验基准 |
| `repoRoot` | `../login_demo` | 读取 / 搜索 / 列目录的上界 |
| `artifactRoot` | `<projectRoot>/artifacts` | 唯一允许写入的目录 |
| `targetArtifact` | `artifacts/login-flow.md` | 本次 Run 的交付物路径 |

工程根由 `import.meta.url` 推导，**不依赖进程工作目录**，因此从任何 cwd 启动都得到同一套路径。

### 4.2 统一工具 Runtime `runtime.ts`

四种工具共用一套受控执行基座，治理顺序固定：

```
未注册工具 → 参数校验（TypeBox + Value.Check）→ 路径边界 → 执行 → 结果治理
```

| 工具 | handler | 边界 |
| --- | --- | --- |
| `list_files` | `listFiles` | 限制在 `repoRoot` 内 |
| `search_code` | `searchCode` | 限制在 `repoRoot` 内，仅扫源码类扩展名 |
| `read_file` | `readSourceFile` | 限制在 `repoRoot` 内 |
| `write_file` | `writeArtifact` | `projectRoot` 解析后必须落在 `artifactRoot` 内 |

关键实现细节：

- 参数 Schema 与 handler **同源注册**，`additionalProperties: false` 拒绝模型臆造的字段。
- 写路径先 `path.resolve` 再判断 `isWithin`，防止 `artifacts/../x` 这类字符串前缀绕过。
- 输出给模型的路径一律相对 `repoRoot`（根表示为 `.`），不泄露本机绝对路径。
- 遍历跳过 `node_modules` / `.git`，每次迭代检查 `signal.throwIfAborted()`。
- 结果只暴露受控结构 `ManagedToolResult { ok, code, modelView, artifact }`；`modelView` 是唯一可进入模型上下文的部分。

错误码（`ToolResultCode`）：

```text
OK / ABORTED / PATH_DENIED / ARTIFACT_PATH_DENIED
INVALID_ARGUMENT / TOOL_NOT_FOUND / TOOL_EXECUTION_FAILED
```

`invoke` 的异常归一顺序：取消信号 → 已知语义化拒绝（`ToolDeniedError`）→ 未知异常归为 `TOOL_EXECUTION_FAILED`。

### 4.3 pi 工具适配层 `pi-tools.ts`

**薄适配，不产生第二套执行逻辑**（不读写文件、不校验路径、不重试、不吞异常）：

1. 用 Runtime 导出的 Schema 描述参数；
2. `execute` 把 `toolCallId / params / signal / context` 原样交给 `runtime.invoke`；
3. 成功 → `modelView` 序列化为文本块，`code` 与 `artifact` 放进 `details`（不占模型上下文）；
4. 失败 → 按 pi 约定**抛出** `Error`，内容为 `{ code, content }`，由 pi 归一化成错误工具结果。

`TOOL_NAMES` 常量在此声明，是适配层与停止决策层共享的**唯二工具命名来源**。四个工具 `executionMode: "sequential"`，避免并发读写同一 workspace 产生竞态。

### 4.4 模型接入层 `model.ts`

把外部 OpenAI-compatible Gateway 包装成 pi 可消费的**逻辑模型**，不含任何 Agent 业务逻辑。

| 常量 | 值 |
| --- | --- |
| `GATEWAY_PROVIDER_ID` | `phase-gateway` |
| `LOGICAL_MODEL_ID` | `agent-default` |
| `GATEWAY_BASE_URL_ENV` | `GATEWAY_BASE_URL`（默认 `http://127.0.0.1:8000/v1`） |
| `GATEWAY_API_KEY_ENV` | `GATEWAY_API_KEY`（无默认值） |

- `gatewayModel`：`api: "openai-completions"`、`reasoning: false`、`contextWindow 128k`、`maxTokens 4096`；成本字段为零值占位，实际统计由 Gateway 负责。
- Provider 通过 `createProvider + openAICompletionsApi()` 注册，鉴权用 `envApiKeyAuth`，密钥只读环境变量。
- 对外只暴露两样东西：`models`（已注册的模型集合）与 `streamGateway = models.streamSimple.bind(models)`，由 `main.ts` 注入装配层。
- 供应商选择、路由、重试、思考开关、Token/成本统计**全部下沉到 Gateway**，Agent 进程不重复实现。

### 4.5 停止决策中心 `loop-guard.ts`

一次 Run 的唯一停止判定点，只读取状态、只做决策。四类职责：

1. **重复动作拦截**：动作指纹 = `sha256(toolName + 递归排序后的 args)`，同一指纹第 3 次在 Runtime handler **执行之前**被 `block`。
2. **最大轮数停止**:`state.turn >= maxTurns` 时以 `MAX_TURNS_EXCEEDED` 停止。
3. **证据收集**：只接受**成功**工具结果的 `args.path`；`read_file` 计入 `readFiles`，`write_file` 计入 `writtenArtifacts`。
4. **完成校验与 Follow-up**：本轮无工具结果（模型想自然结束）时调用 `verifyCompletion()`，返回**缺失项列表**而非布尔值；不满足则注入 `FINAL_REJECTED` Follow-up 继续跑。

`afterTurn` 的判定顺序不可调换：

```
已存在停止原因 → 立即停
达到最大轮数   → MAX_TURNS_EXCEEDED
本轮无工具结果 → 校验证据 → 齐全则 COMPLETED / 不足则注入 Follow-up
本轮有工具结果 → 继续
```

停止原因与常量：

```text
COMPLETED / MAX_TURNS_EXCEEDED / REPEATED_ACTION / ABORTED / MODEL_ERROR
MIN_READ_FILES = 3        // 至少成功读取 3 个源码文件
BLOCK_AT_COUNT = 3        // 第 3 次相同动作被阻止
FINAL_REJECTED            // 证据不足时注入的 Follow-up 标记
```

`stop()` 使用「只在为空时写入」，保证首个停止原因不被覆盖；`markStop()` 供外部事件（取消 / 模型错误）登记原因。本层不调用模型、不执行工具、不访问文件系统，也不实现 Planner / 状态机 / Checkpoint / Sandbox。

### 4.6 装配层 `agent-runner.ts`

把 P1 上下文、P2 模型、P3 工具、P4 停止保护接进 `runAgentLoop`，**不新增任何一层的规则**。

- `USER_TASK` 与 `buildSystemPrompt()` 定义任务与约束（必须先搜索/阅读源码、不得按文件名猜实现、必须产出 `targetArtifact`、必须列出引用路径）。
- `AgentContext` = System Prompt + `createCodeUnderstandingTools(runtime, context)`。
- `AgentLoopConfig` 钩子映射：

| pi 钩子 | 接入实现 |
| --- | --- |
| `convertToLlm` | 只保留 `user / assistant / toolResult` 三类消息 |
| `maxRetries` | `0`（重试由 Gateway 负责） |
| `toolExecution` | `sequential` |
| `beforeToolCall` | `guard.beforeToolCall` → 重复动作拦截 |
| `afterToolCall` | `guard.observeToolResult` → 证据收集 |
| `prepareNextTurn` | 原样返回 `context`（本阶段无 Checkpoint） |
| `shouldStopAfterTurn` | `guard.afterTurn` → 停止判定 |
| `getSteeringMessages` | 恒空 |
| `getFollowUpMessages` | `guard.drainFollowUps` → 注入 `FINAL_REJECTED` |

- `observeEvent()` 负责事件观察与轨迹，不改变 Loop 推进：
  - `turn_start` → `state.turn += 1`；
  - `message_update` → 仅把 `text_delta` 交给 `onText`，不记录隐藏推理；
  - `tool_execution_start/end` → 记录 `toolName + toolCallId`（`toolCallId` 是 Tool Call 与 Tool Result 的连接键）；
  - `turn_end` → 助手消息 `stopReason` 为 `aborted` / `error` 时分别登记 `ABORTED` / `MODEL_ERROR`。

### 4.7 CLI 入口 `main.ts`

最薄的一层：创建 `AbortController`，调用 `runCodebaseAgent`，把模型文本增量写 stdout，最后打印 JSON 摘要：

```json
{ "runId", "turns", "stopReason", "readFiles", "writtenArtifacts", "trace" }
```

该摘要即验收证据：`readFiles` 与 `writtenArtifacts` 来自证据集合，`trace` 可从 `runId → turn → toolCallId` 逐层还原。

---

## 5. 依赖方向与调用链

### 5.1 依赖关系

```
main.ts
  └── agent-runner.ts
        ├── model.ts          （被注入，不反向依赖装配层）
        ├── pi-tools.ts ──► runtime.ts ──► run-context.ts
        ├── loop-guard.ts ──► pi-tools.ts （仅读 TOOL_NAMES）
        └── run-context.ts
```

约束：`runtime.ts` 不认识 pi、不认识 Agent；`loop-guard.ts` 不做 IO；`model.ts` 不含业务逻辑；下层不引用上层。

### 5.2 一次 Run 的主调用链

```
npm start
  → main.ts: runCodebaseAgent({ model, streamFn, signal, onText })
    → agent-runner.ts: 建上下文/状态/guard/tools → runAgentLoop(prompt, ctx, config, onEvent, signal, streamFn)
      → pi: turn_start                                → observeEvent: turn += 1
      → pi: 调 model.streamSimple → model.ts → Gateway(127.0.0.1:8000/v1, agent-default)
      → pi: 解析 tool_calls → beforeToolCall(guard)   → 重复动作？block
      → pi: AgentTool.execute → pi-tools.ts → runtime.invoke → fs 读写
      → pi: afterToolCall(guard)                      → 成功结果收集证据
      → pi: turn_end → shouldStopAfterTurn(guard)     → 停止 / 继续 / 注入 Follow-up
  → agent-runner.ts 返回 { state, messages, trace, runtime }
→ main.ts 打印 stopReason / readFiles / writtenArtifacts / trace
```

---

## 6. 关键契约一览

| 类别 | 取值 | 定义位置 |
| --- | --- | --- |
| 工具名 | `list_files` / `search_code` / `read_file` / `write_file` | `pi-tools.ts` `TOOL_NAMES` |
| 工具结果码 | `OK / ABORTED / PATH_DENIED / ARTIFACT_PATH_DENIED / INVALID_ARGUMENT / TOOL_NOT_FOUND / TOOL_EXECUTION_FAILED` | `runtime.ts` `ToolResultCode` |
| 停止原因 | `COMPLETED / MAX_TURNS_EXCEEDED / REPEATED_ACTION / ABORTED / MODEL_ERROR` | `loop-guard.ts` `StopReason` |
| 运行上限 | `maxTurns` 默认 12；至少读 3 个文件；同动作第 3 次拦截 | `agent-runner.ts` / `loop-guard.ts` |
| 环境变量 | `GATEWAY_BASE_URL`（可选）、`GATEWAY_API_KEY`（必填，无默认值） | `model.ts` |
| 产物路径 | `artifacts/login-flow.md` | `run-context.ts` |

---

## 7. 测试分层

测试与实现层一一对应，`support/harness.ts` 为各层提供隔离环境（临时 `projectRoot` + 只读真实 `login_demo`）。

| 测试文件 | 覆盖层 | 代表性断言 |
| --- | --- | --- |
| `runtime.test.ts` | Runtime | 四能力闭环、越界读拒绝、非产物目录写入拒绝且不落盘、取消提前结束、未注册工具错误码、参数校验失败不计入 handler |
| `pi-tools.test.ts` | 适配层 | 工具注册顺序稳定、`toolCallId`/参数/取消原样透传、`modelView` 才进模型上下文、失败抛错、严格 Schema、无直连文件系统旁路 |
| `loop-guard.test.ts` | 停止决策层 | 第三次动作被阻止且 handler 只执行 2 次、字段顺序不能绕过指纹、达到轮数不再请求模型、证据不足注入 Follow-up、错误结果不计证据、判定顺序不可调换 |
| `loop.test.ts` | 装配层 | Tool Result 回写后继续请求模型、`FINAL_REJECTED` 补齐后成功、重复调用被阻止、最大轮数命中、取消结束、完整闭环轨迹可还原 |
| `model.test.ts` | 模型接入层 | 逻辑模型可被 pi 识别、地址回落与覆盖、密钥只读环境变量、可注入装配层、源码无硬编码密钥与供应商模型名 |

---

## 8. 运行前置条件

`npm start` 只依赖两个环境变量，但要求 Gateway 已在 `127.0.0.1:8000` 运行（模型名 `agent-default`）：

```powershell
# 终端 1：Gateway（week2 项目）
$env:DEEPSEEK_API_KEY="<上游密钥>"
$env:GATEWAY_API_KEY="dev-gateway-key"
$env:GATEWAY_CONFIG="config/gateway.yaml"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir gateway --host 127.0.0.1 --port 8000

# 终端 2：本 Demo
$env:GATEWAY_API_KEY="dev-gateway-key"
npm start
```

注意：Demo 侧**不需要** `DEEPSEEK_API_KEY`，上游密钥只在 Gateway 进程内使用；两个终端的 `GATEWAY_API_KEY` 必须一致。若 Gateway 不可达或上游鉴权失败，第一轮就会以 `MODEL_ERROR` 结束（`main.ts` 的摘要只输出 `stopReason`，底层错误需看 Gateway 日志）。

---

## 9. 设计要点小结

- **循环只有一个**：pi 的 `runAgentLoop`；本项目所有「循环行为」都以钩子形式接入，不另起循环。
- **执行只有一个**：`runtime.invoke`；适配层不提供任何旁路。
- **判定只有一个**：`LoopGuard`；轮数、重复、证据、结束语义都收敛在这里，且停止原因不可覆盖。
- **模型只有一个入口**：`models.streamSimple` 指向 Gateway 的逻辑模型 `agent-default`，供应商差异对 Agent 不可见。
- **证据优先于声明**：`COMPLETED` 必须满足「≥3 个成功读取 + 产物已写入」，模型自称完成不算数。
