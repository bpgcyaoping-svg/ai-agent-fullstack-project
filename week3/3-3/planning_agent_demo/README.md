# Planning Agent Demo

3.2 的配套工程：在 3.1 的 pi Agent Loop 上接入计划层，跑通「修复登录模块到期边界失败测试」的完整闭环。

## 3.3 新增（任务状态 / Checkpoint / 人工接管）

|文件|职责|
|---|---|
|`src/task-record.ts`|R1：任务状态机、步骤记录（工具调用 ID、输入输出、失败原因、重试、决策、证据）与状态迁移日志|
|`src/checkpoint.ts`|R2：存档格式、原子写入、校验和、工作区摘要、存档序号|
|`src/resume.ts`|R3：先检查再恢复，三态判定（completed / not_started / unknown）|
|`src/approval.ts`|R4：补丁暂存、patchHash 绑定参数与文件内容、跨进程重新审批|
|`src/approval-flow.ts`|R4 执行段：批准 → 重算 patchHash → 落盘 → 补证据；人工与 auto 两条入口共用|
|`src/fresh-run.ts`|R5：独立工作区重跑（`--replay`），原记录只读|
|`src/cli.ts`|课堂项目入口：参数、工作区、暂停/审批/恢复/重跑编排与报告输出|
|`src/journal.ts`|pi 低层 Session（JSONL）journal 适配层|
|`scripts/child-run.ts`|脚本化实验入口（与 `npm start` 共用 `src/cli.ts`，支持崩溃点）|
|`tests/checkpoint-resume.test.ts`|A1–A10 验收用例|

### 运行状态机

运行状态是一台显式状态机，每次迁移都记进 `TaskRecord.transitions`
（含自环，例如 `running -(task_interrupted)-> running`）：

```
running ⇄ waiting_tool           发出工具调用 / 工具返回
running → waiting_approval       补丁暂存，等人工决定（不是失败）
waiting_approval → running       批准，回到执行
waiting_approval → suspended     拒绝，停在原地等人工处置
running → suspended              重复动作 / 超轮数 / 模型错误（不是失败）
running → completed | failed | cancelled
```

人工暂停（`--pause-after`）**不改变状态**：任务仍是 `running`，只是停下来，
所以恢复时不会被当成"结果不明"。等待审批是**任务**在等，步骤本身仍是
`in_progress`，只在 `nextDecision` 上标 `await_approval`。

### pi 版本事实（0.84.4）

- 可用：`runAgentLoop` / `runAgentLoopContinue`、全部 Loop Hook、`Session` /
  `JsonlSessionRepo` / `NodeExecutionEnv`。
- **不可用**：`AgentHarness.resume()` 及 `AgentHarness` 其余方法在 0.84.4 是未实现的桩
  （抛 `HarnessNotImplemented`）。因此 R3 的「pi 续跑能力」落在
  低层 journal 判定 + `runAgentLoopContinue` 上。

### 存档位置与格式

存档写在临时工作区内：`<projectRoot>/.agent-runs/<runId>.json`，
信封包含 `schemaVersion`（当前 v2）、`runId`、`createdAt`、`seq`（第几份存档）、
`payload`、`checksum`。写入走「先写 .tmp 再 rename」，失败时保留最近一份有效存档。

### 运行模式

```bash
npm start                                   # 新运行（自动建临时工作区）
npm start -- --workspace <dir>              # 指定工作区
npm start -- --workspace <dir> --pause-after 6 --auto-approve
npm start -- --workspace <dir> --resume <runId>            # 先检查再恢复
npm start -- --workspace <dir> --resume <runId> --approve  # 批准待审批补丁
npm start -- --workspace <dir> --resume <runId> --reject   # 拒绝待审批补丁
npm start -- --workspace <dir> --replay <runId>            # 独立工作区重跑
```

|参数|作用|
|---|---|
|`--workspace <dir>`|工作区；恢复与重跑必须与上次指向同一个工作区|
|`--pause-after <N>`|第 N 个完整轮次结束、存档写盘之后优雅暂停：`stopCode=INTERRUPTED`、进程 exit 0、任务状态仍是 `running`|
|`--auto-approve`|机器放行待审批补丁：仍走暂存、`patchHash` 校验与落盘，只在 `decisions` 里记 `by:"auto"`|
|`--resume <runId>`|先校验存档、工作区与任务状态，再还原业务对象并续跑|
|`--approve` / `--reject`|人工入口：对存档里待审批的补丁作出决定，记 `by:"manual"`|
|`--replay <runId>`|原任务输入 + 初始代码，在独立工作区重跑|
|`--crash-point <name>`|在指定点位 `exit(137)`，模拟异常终止（实验用）|
|`--run-id <id>`|指定 runId|

没有决定就不启动模型：`--resume` 拿到有待审批动作的存档时，只打印待确认内容与
批准/拒绝命令，进程 exit 0。

新运行不静默覆盖：同一工作区里已经有同 `--run-id` 的**未终结**存档
（`running` / `waiting_approval` / `suspended`）时，新运行直接拒绝（exit 3）并提示改用
`--resume <runId>`——否则"暂停 → 继续"的实验做第二次就会把上一次的存档顶掉。
终态存档（`completed` / `failed` / `cancelled`）会被本次新运行覆盖，覆盖前打印一行提示；
想从头再跑一遍请用 `--replay` 或换一个 `--run-id`。

### 课堂实验入口（脚本化模型）

`scripts/child-run.ts` 用 faux 模型驱动同一条 Loop，参数与报告与 `npm start` 完全一致
（两者共用 `src/cli.ts`），便于反复观察中断与审批。`--scenario` 只决定"模型这一轮说什么"。

```bash
WS=$(mktemp -d)

# 场景一：跑到第 6 轮结束即优雅暂停（存档已写、进程 exit 0、任务仍是 running）
npx tsx scripts/child-run.ts --scenario ac_full --workspace "$WS" --run-id run-lab \
  --pause-after 6 --auto-approve
# 从存档检查并续跑：接着旧轮数、旧计划、旧证据继续
npx tsx scripts/child-run.ts --scenario ac_full --workspace "$WS" \
  --resume run-lab --auto-approve

# 场景二：不加 --auto-approve，补丁停在被拦住的地方（源码不变）
npx tsx scripts/child-run.ts --scenario ac4 --workspace "$WS2" --run-id run-fix "<任务描述>"
# 人工批准 / 拒绝（同一个 <runId>，两个进程）
npx tsx scripts/child-run.ts --scenario ac4 --workspace "$WS2" --resume run-fix --approve
npx tsx scripts/child-run.ts --scenario ac4 --workspace "$WS2" --resume run-fix --reject

# 场景三：在独立工作区重跑，原存档只读
npx tsx scripts/child-run.ts --scenario ac4 --workspace "$WS2" --replay run-fix
```

| 参数 | 作用 |
|---|---|
| `--pause-after <N>` | 第 N 轮存档写盘之后优雅暂停；`stopCode=INTERRUPTED`，进程 exit 0，任务状态仍是 `running` |
| `--auto-approve` | 机器放行待审批补丁：仍走暂存、`patchHash` 校验与落盘，只在 `decisions` 里记 `by:"auto"` |
| `--crash-point <name>` | 强制退出（exit 137）模拟异常终止，用于 A1–A3 |
| `--resume <runId>` | 先检查存档/工作区/任务状态，再还原业务对象并续跑 |
| `--approve` / `--reject` | 人工入口：批准或拒绝存档里的待审批补丁（`by:"manual"`）|
| `--replay <runId>` | 原任务输入 + 初始代码，在独立工作区重跑 |

不加 `--auto-approve` 时，补丁停在 `waiting_approval` 且源码不变——这是 R4 的默认语义。
模型侧没有批准工具：批准只能来自人工入口或显式的 `--auto-approve`，
否则"审批"会退化成模型给自己盖章。

### 可反复执行的实验（真实 Gateway）

`npm run lab:cycle` 用 `npm start` 的同一个入口把"中断 → 恢复"整个闭环跑一遍，
并且**每跑一次都换一个新的临时工作区**，所以同一条命令可以反复执行、互不影响：

```bash
npm run lab:cycle                        # 第 6 轮结束优雅暂停 → 从存档恢复续跑，并逐条核对
npm run lab:cycle -- --pause-after 4     # 换暂停点
npm run lab:cycle -- --run-id run-lab2   # 换 runId
npm run lab:cycle -- "<任务描述>"         # 换任务输入
```

第 1 段暂停点是优雅暂停（exit 0、任务状态仍是 `running`）；第 2 段 `--resume` 把存档里的
**会话历史**回灌给 pi 的续跑入口（报告里的"续跑方式"一行会写明），接着旧轮数继续，
而不是把任务输入再发一遍。脚本核对退出码、存档里的会话历史（累积且不重发任务输入）、
轮数与存档序号，全部成立才 exit 0，否则逐条打印 ✗ 并 exit 1。
工作区路径会打印出来，存档在 `<workspace>/.agent-runs/<runId>.json`。
`--workspace <dir>` 复用已有工作区；里面若已有同 runId 的未终结存档，第 1 段会被拒绝——
那正是"该续跑"的场景，请直接用 `npm start -- --workspace <dir> --resume <runId>`。

## 跑起来

```bash
npm install
npm test          # 计划规则 + Loop 集成 + A1–A10 验收，共 32 个用例
npm run build     # tsc 类型检查
npm start         # 接真实 Gateway，模型驱动同一条 Loop，中文输出
```

## 结构

### 目录树

```
planning_agent_demo/
├── package.json                # 项目配置（Node >=22.19, ES module, npm scripts）
├── tsconfig.json               # TypeScript 配置
├── vitest.config.ts            # Vitest 测试配置
│
├── src/                        # 核心源码
│   ├── main.ts                 # 真实 Gateway 入口（接入 model.ts + cli.ts）
│   ├── cli.ts                  # 课堂项目入口：参数解析、工作区准备、三入口编排、报告输出
│   ├── agent-runner.ts         # 装配 pi Loop 与计划层，落档与人工决定
│   ├── model.ts                # 模型注册，接入 Gateway
│   ├── run-context.ts          # 一次 Run 的路径边界，目标/边界/回归三组测试范围
│   ├── runtime.ts              # 受控执行入口：读写文件、apply_patch、run_test，产出证据
│   ├── pi-tools.ts             # 把 Runtime 与计划层适配成 pi 的 AgentTool
│   ├── loop-guard.ts           # 轮数、重复动作、计划门禁、完成契约与 Follow-up
│   ├── plan-store.ts           # 计划状态：依赖检查、证据校验、原子修订、修订预算
│   ├── plan-tools.ts           # create_plan / get_plan / update_plan_step / revise_plan
│   ├── completion-contract.ts  # 完成契约：代码理解任务与修复任务各一份
│   ├── planning-prompt.ts      # 运行提示词与每轮替换的计划快照
│   ├── task-record.ts          # R1：任务状态机、步骤记录与状态迁移日志
│   ├── checkpoint.ts           # R2：存档格式、原子写入、校验和、工作区摘要
│   ├── resume.ts               # R3：先检查再恢复，三态判定
│   ├── approval.ts             # R4：补丁暂存、patchHash 绑定参数与文件内容
│   ├── approval-flow.ts        # R4 执行段：批准 → 重算 patchHash → 落盘 → 补证据
│   ├── fresh-run.ts            # R5：独立工作区重跑，原记录只读
│   └── journal.ts              # pi 低层 Session（JSONL）journal 适配层
│
├── scripts/                    # 脚本入口
│   ├── child-run.ts            # 脚本化实验入口（faux 模型驱动同一 Loop，与 cli.ts 共用）
│   └── lab-cycle.ts            # 可反复执行的「中断 → 恢复」闭环实验
│
├── tests/                      # 测试
│   ├── plan-store.test.ts      # 计划层不变量（12 个用例）
│   ├── agent-runner.test.ts    # Loop 集成：拦截、证据、修订、完成契约（6 个用例）
│   ├── checkpoint-resume.test.ts  # A1–A10 验收：恢复、审批、三个课堂场景（14 个用例）
│   └── support/
│       └── harness.ts          # 测试支撑工具
│
├── fixtures/                   # 目标仓库
│   └── demo-app/
│       ├── src/
│       │   ├── auth/
│       │   │   ├── session-policy.ts  # 待修复的会话过期判断（只比较 UTC 日期）
│       │   │   └── session.ts         # 会话管理
│       │   ├── routes/
│       │   │   └── login.ts           # 登录路由
│       │   └── services/
│       │       └── auth-service.ts    # 认证服务
│       └── tests/
│           ├── session-policy.test.ts   # 目标用例（修复前失败）
│           ├── session-boundary.test.ts # 边界用例（跨 UTC 日界线稳定复现）
│           └── login-flow.test.ts       # 回归用例（修复前后都应通过）
│
└── artifacts/                  # 交付物
    └── login-fix.md            # 修复报告
```

### 代码层级与依赖关系

自上而下分五层：入口层 → 编排层 → 装配层 → 支撑层 → 计划与执行层。
上层只调用下层，同层不互相依赖（除明确标注外）。

```
┌─────────────────────────────────────────────────────────────────┐
│  入口层（Entry）                                                  │
│  src/main.ts ──────────── 真实 Gateway 入口，接入模型 + CLI       │
│  scripts/child-run.ts ─── 脚本化实验入口（faux 模型）             │
│  scripts/lab-cycle.ts ─── 中断→恢复闭环实验脚本                   │
│         │                                                        │
│         ▼                                                        │
│  编排层（Orchestration）                                         │
│  src/cli.ts ──────────── 参数解析、工作区准备、                  │
│                          三入口编排（run / resume / replay）、    │
│                          报告输出                                 │
│         │                                                        │
│         ▼                                                        │
│  装配层（Assembly）                                               │
│  src/agent-runner.ts ─── 装配 pi Loop 与计划层：                  │
│                          Hook 接缝、存档、人工决定、会话历史       │
│         │                                                        │
│         ├──────────────────┬──────────────────┐                  │
│         ▼                  ▼                  ▼                  │
│  支撑层（3.3 新增）  计划层（3.2）       执行层（3.1–3.2）        │
│  task-record.ts     plan-store.ts      runtime.ts               │
│  checkpoint.ts      plan-tools.ts      pi-tools.ts              │
│  resume.ts          completion-        loop-guard.ts            │
│  approval.ts        contract.ts        run-context.ts           │
│  approval-flow.ts   planning-prompt.ts model.ts                 │
│  fresh-run.ts                                                    │
│  journal.ts                                                      │
└─────────────────────────────────────────────────────────────────┘
```

各层职责与文件对应：

**入口层**

|文件|职责|
|---|---|
|`src/main.ts`|真实 Gateway 入口：接入 `model.ts` 的模型 + `cli.ts` 的编排，SIGINT 映射为优雅暂停|
|`scripts/child-run.ts`|脚本化实验入口：faux 模型驱动同一 `cli.ts`，支持崩溃点注入，输出 RESULT_JSON 供验收断言|
|`scripts/lab-cycle.ts`|闭环实验：「中断 → 恢复」串成一段脚本，逐条核对退出码、会话历史、轮数与存档序号|

**编排层**

|文件|职责|
|---|---|
|`src/cli.ts`|参数解析（`--workspace` / `--resume` / `--approve` 等）、工作区准备（复制初始代码）、三入口编排（run / resume / replay）、终态存档冲突拒绝、报告输出（状态迁移、步骤、测试、证据）|

**装配层**

|文件|职责|
|---|---|
|`src/agent-runner.ts`|装配 pi Loop 与计划层：Hook 接缝（beforeToolCall / afterToolCall / prepareNextTurn / shouldStopAfterTurn）、存档写盘（persist）、人工决定落地（operatorDecision）、会话历史累积（message_end + turn_end）、续跑入口选择（prompt vs continue）、崩溃点注入|

**支撑层（3.3 新增：任务状态 / Checkpoint / 人工接管）**

|文件|职责|
|---|---|
|`src/task-record.ts`|R1：任务状态机（running / waiting_tool / waiting_approval / suspended / completed / failed / cancelled）、步骤记录（工具调用 ID、输入输出、失败原因、重试、决策、证据）、状态迁移日志（跨进程续号）|
|`src/checkpoint.ts`|R2：存档格式（schemaVersion v2 + checksum）、原子写入（tmp + rename）、工作区摘要（repoDigest）、存档序号|
|`src/resume.ts`|R3：固定顺序恢复（校验存档 → 校验工作区 → 校验任务状态 → 还原 → 续跑）、三态判定（completed / not_started / unknown）、待审批动作带出|
|`src/approval.ts`|R4：补丁暂存（stage）、patchHash 计算（path + search + replace + 文件内容）、批准/拒绝/过期判定（APPROVAL_STALE）|
|`src/approval-flow.ts`|R4 执行段：批准 → 重算 patchHash → 落盘 → 补证据；人工（manual）与 auto 两条入口共用|
|`src/fresh-run.ts`|R5：独立工作区重跑（--replay），原存档只读，新 runId，不继承危险权限|
|`src/journal.ts`|pi 低层 Session（JSONL）journal 适配层：操作开始/工具启动/操作结束落盘，供恢复时判定操作边界|

**计划层（3.2）**

|文件|职责|
|---|---|
|`src/plan-store.ts`|Plan / PlanStep / Evidence / PlanStore / PlanningSession：依赖检查（readySteps）、证据校验、原子修订（revise_plan）、修订预算（maxRevisions）|
|`src/plan-tools.ts`|计划工具的契约与 Handler：`create_plan` / `get_plan` / `update_plan_step` / `revise_plan`，模型只能提出操作，Handler 负责校验|
|`src/completion-contract.ts`|完成契约：代码理解任务（读够源码 + 交付文档）与修复任务（改动 + 通过测试 + 交付说明）各一份，可注入|
|`src/planning-prompt.ts`|运行提示词（PLANNING_PROMPT）与每轮替换的计划快照（renderPlanSnapshot）|

**执行层（3.1–3.2）**

|文件|职责|
|---|---|
|`src/runtime.ts`|受控执行入口：list_files / search_code / read_file / write_file / `apply_patch` / `run_test`，产出结构化证据（diff、exitCode），只由 Runtime 写入证据|
|`src/pi-tools.ts`|把 Runtime 与计划层适配成 pi 的 AgentTool：工具 Schema 定义、参数校验、GOVERNED_TOOLS 门禁（写操作必须携带 planStepId）|
|`src/loop-guard.ts`|Loop 守卫：轮数限制、重复动作检测、计划门禁（beforeToolCall 拦截未就绪步骤）、完成契约判定（shouldStopAfterTurn）、Follow-up 注入|
|`src/run-context.ts`|一次 Run 的路径边界：projectRoot / repoRoot / artifactRoot / targetSource / 三组测试套件范围|
|`src/model.ts`|模型注册：接入 Gateway（OpenAI 兼容协议），环境变量读取 API Key|

## 案例

`fixtures/demo-app/src/auth/session-policy.ts` 的 `isSessionExpired` 只比较 UTC 日期，
会话会晚最多 24 小时失效。CI 里只在跨 UTC 日界线时偶发失败，课堂用固定时钟在
`tests/session-boundary.test.ts` 里稳定复现。

修复前 `tests/session-policy.test.ts` 与 `tests/session-boundary.test.ts` 失败，
`tests/login-flow.test.ts` 通过。修复只改 `session-policy.ts`，公共 API 不变。

## 课堂观察点

1. `modifyPasswordLogic` 在 `reproduce` 完成前不能启动。
2. 没有证据、证据不属于该步骤、证据满足不了验收条件，三种情况都不能完成。
3. `revise_plan` 保留旧步骤并标记 `skipped`，下游依赖原子改接到新步骤。
4. 修订必须带理由和证据，并受 `maxRevisions` 限制；校验失败时原计划一字不变。
5. 当前代码版本之外的测试通过结果不能用于交付。
6. 计划已创建时，写入、改代码、跑测试必须携带 `planStepId`，否则在执行前被拦下。
7. 模型提前给出 Final Answer 不会结束任务，完成契约会拒绝并注入 Follow-up。

## 3.3 课堂观察点

1. 暂停（`suspended`）与等待审批（`waiting_approval`）不算任务失败。
2. 工具返回 `ok:true` 不等于测试通过：测试退出码才是验证完成的依据。
3. 恢复固定顺序：校验存档 → 校验工作区 → 校验任务状态 → 还原 → 续跑。
4. 结果不明的写操作绝不自动重放，只输出核查信息。
5. 批准前与拒绝后都不执行补丁；批准只对对应 actionId 生效。
6. 补丁参数或文件内容变化后，旧授权因 `patchHash` 失效（`APPROVAL_STALE`）。
7. 进程重启不默认获得批准（`approved` 由内存决定）；重启后要改代码，必须由人工
   重新批准，或由显式的 `--auto-approve` 放行。
8. 重跑使用独立工作区，原存档字节级不变。
9. `--pause-after N` 是优雅暂停：存档先落盘再停，进程 exit 0，任务状态仍是
   `running`（不是 `failed`，也没终结）；恢复时能直接判定可续跑，不会落进「结果不明」。
10. `--auto-approve` 不绕过审批：补丁仍要暂存、仍要算 `patchHash`、仍要落盘校验，
    区别只在 `decisions[].by` 记成 `auto`，人工批准记成 `manual`。
11. 恢复后新增证据的 `ev-N` 序号接在旧序号之后，既不重号也不回退——
    这是跨进程续跑能通过 `PLAN_EVIDENCE_STALE` 与完成契约的前提。
12. 状态迁移是证据：`#seq from -(event)-> to（detail）` 逐条写出"为什么停在这里、
    恢复后接着谁"，`waiting_tool` / `waiting_approval` / `task_interrupted` 都能被核对。
13. 模型侧没有批准入口：批准只能来自人工 `--approve` 或显式的 `--auto-approve`。
14. 没有决定就不启动模型：遇到待审批动作的 `--resume` 只打印待确认内容与两条命令。
15. 报告必须给出测试退出码与写入产物：最终状态只是入口，文件改动和验证记录才是依据。
16. 一次补丁只留一条 diff 证据（审批执行段登记后，工具结果不再重复登记）。
