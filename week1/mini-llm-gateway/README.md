# mini-llm-gateway

一个统一的大模型调用服务：对外只暴露一套 OpenAI 风格的接口，内部用**适配器模式**封装两种完全不同的上游协议，并按请求里的 `model` 字段动态路由。

| 对外模型名 | 上游协议 | DeepSeek 模型 |
| --- | --- | --- |
| `deepseek-v4-pro` | OpenAI Responses API（`POST /v1/responses`） | `deepseek-v4-pro` |
| `deepseek-flash` | Anthropic Messages API（`POST /v1/messages`） | `deepseek-flash` |

## 功能总览

| 能力 | 实现位置 | 说明 |
| --- | --- | --- |
| 统一抽象层 | `app/adapters/base.py`、`app/services/gateway.py` | `AdapterRequest → PreparedRequest → AdapterResult / StreamParser`，屏蔽鉴权、请求体、返回格式差异 |
| 流式输出 | 两个适配器的 `stream_parser()` + `GatewayService.stream()` | 上游 SSE 被归一化为 `data: {...}` 块，末尾 `data: [DONE]` |
| 结构化输出 | `app/services/structured.py` + 适配器 | Responses 用原生 `text.format`；Anthropic 无该字段，改用强制工具调用；统一做 jsonschema 校验，失败自动修复重试一次 |
| 提示词版本管理 | `app/services/prompts.py` | SQLite 存储、Jinja2 沙箱渲染（`StrictUndefined`）、按 `version` 引用或使用激活版本 |
| 可观测性 | `app/services/observability.py` | 每次调用的 prompt/completion/cached/reasoning token、总延迟、首 Token 延迟、重试次数、状态码 |
| 统一错误码 | `app/core/errors.py` | 所有出口错误都是 `{"error": {...}}` 结构 |
| 指数退避重试 | `app/core/retry.py`、`gateway.py` | 最多 3 次尝试，`base_delay * 2^n` + 抖动，只重试可重试错误 |
| 按模型限流 | `app/core/rate_limit.py` | 每个模型独立 Token Bucket，超限返回 429 + `Retry-After` |

## 目录结构

```
mini-llm-gateway/
├── app/
│   ├── main.py                  # FastAPI 工厂与生命周期
│   ├── config.py                # YAML 配置 + ${ENV:-default} 展开 + .env 加载
│   ├── schemas.py               # Pydantic 请求/响应模型
│   ├── api/routes.py            # 全部 HTTP 路由
│   ├── adapters/
│   │   ├── base.py              # 统一适配器协议
│   │   ├── openai_responses.py  # OpenAI Responses 适配器
│   │   └── anthropic_messages.py# Anthropic Messages 适配器
│   ├── core/{errors,retry,rate_limit}.py
│   └── services/
│       ├── gateway.py           # 路由、重试、流式编排、用量落库
│       ├── prompts.py           # 版本化提示词仓库
│       ├── structured.py        # JSON 提取 + jsonschema 校验
│       └── observability.py     # SQLite 用量账本与聚合指标
├── scripts/
│   ├── mock_upstream.py         # 两种协议的确定性 mock 上游（含故障注入）
│   └── verify.py                # 端到端验收脚本（mock / real 双模式）
├── tests/                       # pytest：路由、流式、结构化、重试、限流、模板
├── config.yaml
├── requirements.txt
└── .env.example
```

## 启动指南

```bash
cd week1/mini-llm-gateway

pip install -r requirements.txt

# 配置密钥（至少 DEEPSEEK_API_KEY）
cp .env.example .env        # Windows: copy .env.example .env
# 编辑 .env: DEEPSEEK_API_KEY=sk-xxx

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/v1/models
```

`config.yaml` 支持 `${ENV_VAR:-默认值}` 环境变量插值，`database_path` 相对于配置文件解析。若设置 `GATEWAY_API_KEY`（或 `api_keys`），除 `/healthz`、`/readyz` 外所有接口都需要 `Authorization: Bearer <key>`。

## curl 示例

### 1. 普通调用（自动路由到两个协议）

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v4-pro",
    "messages": [{"role": "user", "content": "用一句话解释什么是网关"}],
    "max_tokens": 256
  }'

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-flash",
    "messages": [{"role": "user", "content": "用一句话解释什么是网关"}],
    "max_tokens": 256
  }'
```

### 2. 流式输出（SSE）

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v4-pro",
    "stream": true,
    "messages": [{"role": "user", "content": "从 1 数到 5"}]
  }'
```

返回形如：

```
data: {"id":"req_...","object":"chat.completion.chunk","model":"deepseek-v4-pro","choices":[{"index":0,"delta":{"role":"assistant","content":"1"}}]}
...
data: {"id":"req_...","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":94,"completion_tokens":46,...},"timing":{"latency_ms":2100.5,"ttft_ms":1419.39},"retries":0}
data: [DONE]
```

### 3. 结构化输出

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-flash",
    "messages": [{"role": "user", "content": "给出答案 42，置信度 99"}],
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "answer",
        "schema": {
          "type": "object",
          "properties": {"answer": {"type": "string"}, "confidence": {"type": "integer"}},
          "required": ["answer", "confidence"],
          "additionalProperties": false
        }
      }
    }
  }'
```

`response_format.type` 支持 `text`（默认）、`json_object`、`json_schema`。返回体里除了 `choices[0].message.content`，还有校验后的 `structured_output`。若模型输出不合法，网关会带着修复指令再请求一次，仍失败则返回 422 `structured_output_invalid`。

### 4. 提示词版本管理

```bash
# 创建 v1（同一 id 再次创建即 v2，并自动激活）
curl -s http://127.0.0.1:8000/v1/prompts \
  -H "Content-Type: application/json" \
  -d '{"id":"analyst","name":"Analyst","role":"system","content":"You are a {{ tone }} analyst. Answer in {{ language }}."}'

# 渲染显式版本
curl -s http://127.0.0.1:8000/v1/prompts/analyst/render \
  -H "Content-Type: application/json" \
  -d '{"version":1,"variables":{"tone":"strict","language":"English"}}'

# 渲染激活版本（不传 version）
curl -s http://127.0.0.1:8000/v1/prompts/analyst/render \
  -H "Content-Type: application/json" \
  -d '{"variables":{"tone":"friendly","language":"Chinese"}}'

# 调用时引用模板（version 可省略，表示使用激活版本）
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v4-pro",
    "messages": [{"role": "user", "content": "Say ok."}],
    "prompt": {"id":"analyst","version":1,"variables":{"tone":"strict","language":"English"}}
  }'
```

模板渲染使用 Jinja2 沙箱 + `StrictUndefined`：缺少变量会返回 422 `prompt_render_error`，而不是静默填空。

### 5. 可观测性

```bash
# 最近调用明细（含 token 分类、延迟、TTFT、重试、状态）
curl -s "http://127.0.0.1:8000/admin/usage?limit=20"
curl -s http://127.0.0.1:8000/admin/usage/req_xxxxxxxx

# 按模型/协议聚合
curl -s http://127.0.0.1:8000/admin/metrics

# 当前各模型令牌桶余量
curl -s http://127.0.0.1:8000/admin/rate-limits
```

### 6. 限流与错误

默认 `deepseek-v4-pro` 为 20 rpm / burst 3。突发超限时：

```bash
$ curl -i http://127.0.0.1:8000/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}]}'
HTTP/1.1 429 Too Many Requests
Retry-After: 59

{"error":{"message":"Rate limit exceeded for model 'deepseek-v4-pro'; retry in 59s","type":"rate_limit_error","code":"rate_limit_exceeded","param":null,"request_id":"req_..."}}
```

限流桶按模型独立：`deepseek-v4-pro` 被限流不影响 `deepseek-flash`。

## 错误码

所有错误统一为：

```json
{"error": {"message": "...", "type": "...", "code": "...", "param": null, "request_id": "req_...", "details": "可选"}}
```

| HTTP | code | 场景 |
| --- | --- | --- |
| 400/422 | `invalid_request` | 请求体不合法、无有效消息 |
| 401 | `invalid_api_key` | 网关鉴权失败（配置了 `GATEWAY_API_KEY` 时） |
| 404 | `model_not_found` | `model` 不在配置中 |
| 404 | `prompt_not_found` | 提示词 id / 版本不存在 |
| 422 | `prompt_render_error` | 模板变量缺失或渲染失败 |
| 422 | `structured_output_invalid` | 结构化输出修复后仍不合法 |
| 429 | `rate_limit_exceeded` | 本模型令牌桶耗尽（附 `Retry-After`） |
| 429 | `upstream_rate_limit` | 上游限流，可重试 |
| 502 | `upstream_auth_error` | 上游 401/403（网关密钥配置错误） |
| 502 | `upstream_error` | 上游其它 5xx / 连接失败 / 非法 JSON |
| 502/504 | `upstream_timeout` | 上游超时（可重试） |
| 500 | `gateway_misconfigured` | 未配置 `api_key_env` 指向的密钥 |

可重试错误（408/409/429/5xx/超时/连接失败）最多尝试 3 次，退避 `base_delay * 2^n` 并叠加 25% 抖动；调用记录里的 `retries` 是实际重试次数。

## 验证

### 自动化测试（确定性，不访问外网）

```bash
python -m pytest -q
# 8 passed
```

覆盖：按 model 路由并屏蔽协议差异、SSE 流式与 TTFT 落库、结构化校验与修复、结构化失败 422、提示词版本与变量渲染、指数退避重试、按模型独立限流、错误信封 + 指标 + 网关鉴权。

### 端到端验收脚本

```bash
# 确定性模式：自动拉起 mock 上游 + 网关，不需要 API Key
python scripts/verify.py --mode mock

# 真实模式：调用真实 DeepSeek 两个协议端点
python scripts/verify.py --mode real
# 或指定 env 文件：python scripts/verify.py --mode real --env-file ../.env
```

脚本会自行选择空闲端口、生成临时配置与 SQLite 库、拉起子进程，结束后自动清理。每项打印 PASS/FAIL，证据写入：

- `evidence/verify_mock.json`
- `evidence/verify_real.json`

覆盖的功能点：

1. `model_routing_endpoint`：两个模型的协议映射
2. `non_stream::<model>`：两个模型非流式调用
3. `streaming_sse::<model>`：两个模型 SSE 流式 + 首 Token 延迟
4. `structured_output::<model>`：两个模型 json_schema 输出
5. `prompt_versions_and_reference`：v1/v2 存储、显式版本渲染、激活版本渲染、调用引用与落库版本
6. `observability`：用量明细、token 分类、延迟、TTFT、聚合指标
7. `error_envelope`：未知模型 404 统一错误结构
8. `retry_exponential_backoff`（mock 模式）：`__FAIL_TWICE__` 注入两次 503，验证最终 200、`retries=2`、上游被请求 3 次
9. `rate_limit_per_model`：严格限流实例上 pro 首次 200、第二次 429 + `Retry-After`，flash 仍 200

真实模式最近一次结果（`evidence/verify_real.json`）：11/11 通过，pro TTFT ≈1419ms、flash TTFT ≈751ms，两个模型结构化输出均为 `{"answer": "42", "confidence": 99}`。

## mock 上游

`scripts/mock_upstream.py` 同时模拟两种协议，便于离线验证重试与限流：

```bash
python scripts/mock_upstream.py --port 9001
# POST /v1/responses            OpenAI Responses
# POST /anthropic/v1/messages   Anthropic Messages
# GET  /__stats                 各模型请求计数
# POST /__reset                 清零
```

在用户消息中写入魔术字符串可注入故障：

| 标记 | 行为 |
| --- | --- |
| `__FAIL_ONCE__` | 首次请求返回 503 |
| `__FAIL_TWICE__` | 前两次请求返回 503 |
| `__RATE_LIMIT_ONCE__` | 首次请求返回 429 |

## 设计要点

- **协议差异收敛在适配器**：`prepare()` 负责鉴权头与请求体形态，`parse_response()` 负责非流式解析，`stream_parser()` 负责 SSE 事件状态机；网关只消费统一结构。
- **Anthropic 的结构化输出**：Messages API 没有 `response_format`，适配器把 JSON Schema 转成唯一工具并强制 `tool_choice`；由于 DeepSeek 思考模式不支持强制工具调用，适配器同时下发 `thinking: {"type":"disabled"}`。
- **缓存/推理 token**：Responses 取 `input_tokens_details.cached_tokens` 与 `output_tokens_details.reasoning_tokens`；Anthropic 把 `cache_read_input_tokens` 计入 cached、`cache_creation_input_tokens` 计入 prompt。
- **流式重试安全**：只有在尚未向下游输出任何文本时才允许重试，避免重复内容；已输出后失败则以 `event: error` + `[DONE]` 收尾。
