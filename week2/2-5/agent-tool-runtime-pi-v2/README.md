# Agent Tool Runtime on pi

本项目把阶段一的 LiteLLM 统一模型网关，升级为基于 pi 的 Agent 工具调用基础设施。模型、CLI、`pi-coding-agent` 扩展和审批 API 共用同一个 `ToolRuntime`；正式 MCP 连接 `my-coffee`。

## 1. 环境

- Python 3.12+
- Node.js 22.19+
- DeepSeek API Key
- 瑞幸 MCP Token

```bash
cp .env.example .env
# 手工将四个值写入当前终端环境；项目不会自动读取或提交明文 .env。
export DEEPSEEK_API_KEY="..."
export GATEWAY_API_KEY="..."
export APPROVAL_API_KEY="..."
export LUCKIN_MCP_TOKEN="..."
```

正式 MCP 配置位于 `config/platform.yaml`：

```yaml
mcp_servers:
  - id: my-coffee
    type: streamablehttp
    url: https://gwmcp.lkcoffee.com/order/user/mcp
    headers:
      Authorization: Bearer ${LUCKIN_MCP_TOKEN}
```

## 2. 安装与启动

```bash
python -m venv .venv
.venv/bin/pip install -e 'gateway[dev]'
cd runtime && npm ci && cd ..

GATEWAY_CONFIG=config/gateway.yaml \
  .venv/bin/python -m uvicorn app.main:app --app-dir gateway --host 127.0.0.1 --port 8000
```

另开终端：

```bash
cd runtime
npm run cli -- tools
npm run cli -- agent "查询订单 ORD-A-1001 的状态"
npm run approval-api
```

`tools` 和 `agent` 默认连接正式 `my-coffee`。只验收本地工具时显式加 `--without-mcp`，这不会替换正式 MCP，仅用于隔离测试。

## 3. 审批

模型发起高风险工具后，Loop 停止并返回 `pending_approval_id`。审批与参数摘要、用户、租户、Trace 和 Tool Call 绑定，参数变化会使旧审批失效。

```bash
npm run cli -- approvals list
npm run cli -- approvals decide <approval-id> --decision approve --actor teacher
npm run cli -- approvals execute <approval-id>
```

审批 API：

```bash
curl -H "Authorization: Bearer $APPROVAL_API_KEY" http://127.0.0.1:8787/approvals?status=pending
curl -X POST -H "Authorization: Bearer $APPROVAL_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve","actor":"teacher"}' \
  http://127.0.0.1:8787/approvals/<approval-id>/decision
curl -X POST -H "Authorization: Bearer $APPROVAL_API_KEY" \
  http://127.0.0.1:8787/approvals/<approval-id>/execute
```

## 4. pi 两种入口

Headless 产品入口：

```bash
npm run cli -- agent "帮我完成任务"
```

交互式 Coding Agent 入口：

```bash
npm run pi:managed -- \
  --managed-user-id trainer-1 \
  --managed-tenant-id tenant_a \
  --managed-roles supervisor \
  --managed-mode plan
```

## 5. 测试

```bash
cd runtime
npm run build
npm test
npm run test:mcp

cd ../gateway
../.venv/bin/pytest

cd ../runtime
npm run test:live       # 缺 DEEPSEEK_API_KEY 时必须失败，绝不回退到假模型
npm run test:mcp:live   # 缺 LUCKIN_MCP_TOKEN 时必须失败
```

确定性测试覆盖 Schema、Registry、RBAC、租户边界、审批绑定、幂等、重试、降级、脱敏、Trace、Loop Budget 与 MCP Adapter。`test:live` 才是模型决策验收，必须真实调用 DeepSeek。

## 6. 安全约束

- Tool Call 是候选动作，不是可信指令。
- `my-coffee` 未知远端工具默认 `high` 风险并要求确认。
- Token 只从环境变量读取，审计层按敏感字段和通用密钥字段脱敏。
- 只有幂等且可重试的调用才自动重试；流式输出已经发出后不切换模型。
- pi Hook 修改后的参数不会由 pi 自动重验，因此 handler 前统一由 `ToolRuntime` 再验证。
- 审计写入失败不能改变已经完成的业务副作用；生产环境应将审计发送到独立可靠队列。

## 7. 调用链路

### 7.1 组件与依赖

```mermaid
flowchart LR
  subgraph Entries[入口进程]
    CLI[runtime/src/cli.ts<br/>tools / agent / approvals / audit / trace / invoke]
    EXT[runtime/src/pi/coding-agent-extension.ts<br/>pi-coding-agent 扩展]
    API[runtime/src/approval-server.ts<br/>HTTP :8787]
  end

  subgraph Build[Platform 组装 platform.ts]
    CFG[config.ts<br/>loadConfig]
    STORE[store.ts<br/>PlatformStore · SQLite]
    POLICY[policy.ts<br/>PolicyEngine]
    REG[registry.ts<br/>ToolRegistry]
    MCP[mcp/adapter.ts<br/>McpAdapterPlugin]
    RT[runtime.ts<br/>ToolRuntime]
  end

  subgraph Tools[内置工具]
    ORDER[tools/order.ts<br/>order.get_status]
    TICKET[tools/ticket.ts<br/>ticket.create]
  end

  subgraph GatewayProc[gateway 进程 Python/FastAPI :8000]
    GWMAIN[app/main.py<br/>/v1/chat/completions<br/>/v1/prompts/:name/render<br/>/internal/tools/order.get_status]
    GWSVC[app/service.py<br/>LiteLLMGateway]
    GWPROMPT[app/prompts.py<br/>PromptRegistry]
    GWREPO[app/repository.py<br/>GatewayRepository]
  end

  EXT_MCP[(my-coffee MCP<br/>Streamable HTTP)]
  EXT_DS[(DeepSeek API)]

  CLI -->|buildPlatform| Build
  EXT -->|buildPlatform| Build
  API -->|buildPlatform| Build
  CFG --> STORE
  CFG --> POLICY
  CFG --> REG
  CFG --> MCP
  REG -->|register| ORDER
  REG -->|register| TICKET
  MCP -->|register mcp.my-coffee.*| REG
  MCP --- EXT_MCP
  RT --> REG
  RT --> POLICY
  RT --> STORE
  ORDER -->|HTTP /internal/tools/order.get_status| GWMAIN
  EXT & CLI -->|HTTPS /v1/chat/completions<br/>/v1/prompts/render| GWMAIN
  GWMAIN --> GWSVC
  GWMAIN --> GWPROMPT
  GWSVC --> GWREPO
  GWSVC --- EXT_DS
```

### 7.2 Agent Loop 主链路（`npm run cli -- agent`）

```mermaid
sequenceDiagram
  autonumber
  actor U as 用户
  participant CLI as cli.ts agent
  participant AR as pi/agent-runner.ts<br/>runManagedAgent
  participant PC as pi/prompt-client.ts
  participant GW as Gateway :8000
  participant DS as DeepSeek
  participant PI as pi runAgentLoop
  participant LG as LoopGuard
  participant TR as ToolRuntime
  participant RG as ToolRegistry
  participant PE as PolicyEngine
  participant ST as PlatformStore
  participant TL as Tool Handler
  participant MCP as my-coffee MCP

  U->>CLI: agent "查询订单 ORD-A-1001"
  CLI->>AR: runManagedAgent(platform, input)
  AR->>PC: renderSystemPrompt(config, ctx)
  PC->>GW: POST /v1/prompts/agent.default/render
  GW-->>PC: { prompt, version, content_hash }
  AR->>AR: createGatewayModels → provider 指向 gateway
  AR->>RG: snapshot(ctx, policy) 过滤可见工具
  AR->>PI: runAgentLoop(messages, tools, hooks)

  loop 每轮对话（受 LoopGuard 预算约束）
    PI->>GW: POST /v1/chat/completions (stream)
    GW->>DS: litellm.acompletion
    DS-->>GW: chunks
    GW-->>PI: SSE 流
    PI-->>U: text_delta → onText
  end

  PI->>LG: beforeToolCall（轮次/工具数/重复/时限）
  LG-->>PI: allow / deny
  PI->>TR: beforePiCall → prepare
  TR->>RG: getByModelName
  TR->>TR: validateToolArguments (pi-ai)
  TR->>PE: evaluate（白名单/RBAC/plan/租户前缀/审批）
  alt deny
    TR->>ST: addAudit + addTrace tool.denied
    TR-->>PI: block
  else confirm
    TR->>ST: createApproval + tool.approval_requested
    TR-->>PI: block + approval_id
  else allow
    TR-->>PI: prepared 缓存
  end

  PI->>TR: AgentTool.execute → executePreparedById
  TR->>TL: runWithDeadline(handler, 超时, 重试)
  alt order.get_status
    TL->>GW: /internal/tools/order.get_status
  else ticket.create
    TL->>ST: createTicket（幂等键）
  else mcp.my-coffee.*
    TL->>MCP: client.callTool
  end
  TL-->>TR: output / ToolRuntimeError
  opt 可重试错误且有 fallbackHandler
    TR->>TL: fallbackHandler（不重试）
  end
  TR->>ST: addAudit + addTrace tool.completed / failed
  TR-->>PI: tool result

  PI->>LG: shouldStopAfterTurn（pendingApproval? / 预算?）
  LG-->>PI: stop / continue
  PI-->>AR: messages
  AR-->>CLI: { trace_id, final_answer, stop, budget, pending_approval_id }
```

### 7.3 审批执行链路（`approvals decide` → `execute` / Approval API）

```mermaid
sequenceDiagram
  autonumber
  actor Op as 审批人
  participant CLI as cli.ts approvals
  participant API as approval-server.ts :8787
  participant ST as PlatformStore
  participant TR as ToolRuntime
  participant RG as ToolRegistry
  participant PE as PolicyEngine
  participant TL as Tool Handler

  Op->>API: POST /approvals/:id/decision
  API->>ST: decideApproval(approve/reject, actor)
  Note over ST: 仅当 status=pending 且未过期
  Op->>API: POST /approvals/:id/execute
  API->>TR: executeApproval(id)
  TR->>ST: getApproval → 必须 status=approved
  TR->>TR: approvalDigest 校验 (trace_id, tool_call_id,<br/>tool_name, user, tenant, args)
  Note over TR: 参数变化 → APPROVAL_BINDING_MISMATCH
  TR->>ST: claimApproval (CAS: approved → executing)
  TR->>RG: getByModelName + validateToolArguments
  TR->>PE: evaluate(approvalSatisfied=true)
  TR->>TL: executePrepared → runWithDeadline(handler)
  TL-->>TR: result / ToolRuntimeError
  TR->>ST: finishApproval(consumed / failed)<br/>+ addAudit + addTrace
  TR-->>API: InvocationResult
```

CLI 的 `approvals decide` / `approvals execute` 与 Approval API 走完全相同的 `PlatformStore` + `ToolRuntime.executeApproval` 路径，只是入口不同；`invoke` 子命令绕过模型但仍走 `ToolRuntime.invoke`，用于确定性验收。

