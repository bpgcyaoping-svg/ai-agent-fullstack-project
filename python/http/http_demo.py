import httpx
import time


# 这就是上面那段文本协议的 Python 版本
# response = httpx.get(
#     "https://api.deepseek.com/v1/models",
#     headers={
#         "Authorization": "Bearer sk-xxxxxxxxxxxxxxxxxxxx",
#         "Content-Type": "application/json",
#     },
# )

# print(response.status_code)   # 200
# print(response.json())         # {"object": "list", "data": [...]}

response = httpx.post(
    "https://api.deepseek.com/v1/chat/completions",
    headers={
        "Authorization": "Bearer sk-7867b2a64cc04bbeb343255a86ccfef5",
        "Content-Type": "application/json",
    },
    json={                                # ← 这个 json 参数会自动序列化并放进 Body
        "model": "deepseek-flash",  
        "messages": [{"role": "user", "content": "写一篇 1000 字的 AI Agent 介绍"}],
        "stream": True,
    },
    timeout=120,
)

# 逐行读取返回的 SSE 事件流
for line in response.iter_lines():
    if line.startswith("data: "):
        data_str = line[6:]  # 去掉 "data: " 前缀
        if data_str == "[DONE]":
            break
        import json
        chunk = json.loads(data_str)
        delta = chunk["choices"][0].get("delta", {}).get("content", "")
        if delta:
            print(delta, end="", flush=True)   # ← 实时打印，一个字一个字出来


# # 调用 LLM API 时，这些状态码会和你不期而遇
# STATUS_MAP = {
#     200: "OK — 一切正常，解析 data 就行",
#     201: "Created — POST 创建资源成功",
#     400: "Bad Request — 你的请求格式有问题，检查 JSON / 参数名",
#     401: "Unauthorized — API Key 不对或过期了",
#     403: "Forbidden — 有 Key 但没权限访问这个资源",
#     404: "Not Found — 请求的资源不存在（接口路径拼错了？模型名写错了？）",
#     429: "Too Many Requests — 被限流了！等几秒再重试（这是 Agent 最常见的问题）",
#     500: "Internal Server Error — 服务端挂了，重试（加 exponential backoff）",
#     502: "Bad Gateway — 网关 / 代理出问题，同上",
#     503: "Service Unavailable — 服务暂时不可用，等一会重试",
# }


# # 实战：健壮的 LLM API 调用
# import httpx
# import time

# def call_llm_with_retry(url: str, headers: dict, body: dict, max_retries: int = 3):
#     """调用 LLM API，自动处理常见错误"""
#     for attempt in range(max_retries):
#         response = httpx.post(url, headers=headers, json=body, timeout=60)

#         if response.status_code == 200:
#             return response.json()

#         elif response.status_code == 429:
#             # 限流——等 Retry-After 头或指数退避
#             wait = int(response.headers.get("Retry-After", 2 ** attempt))
#             print(f"[429] 限流，{wait}秒后重试（第 {attempt + 1} 次）")
#             time.sleep(wait)

#         elif response.status_code in (500, 502, 503):
#             # 服务端错误——指数退避重试
#             wait = 2 ** attempt
#             print(f"[{response.status_code}] 服务端错误，{wait}秒后重试")
#             time.sleep(wait)

#         elif response.status_code == 401:
#             raise Exception("API Key 无效！请检查密钥。")

#         else:
#             raise Exception(f"请求失败 [{response.status_code}]: {response.text}")

#     raise Exception(f"重试 {max_retries} 次后仍失败")

# headers = {
#     # 1. 认证 — 最常用，每次调 LLM API 必带
#     "Authorization": "Bearer sk-xxxxxxxxxxxxxxxxxxxx",
#     # 2. 告诉服务端 Body 是什么格式
#     "Content-Type": "application/json",
#     # 3. 告诉服务端我能接收什么格式（通常也用 json）
#     "Accept": "application/json",
#     # 4. User-Agent — 标明调用方身份（OpenAI 可能会根据这个限流 / 过滤）
#     "User-Agent": "MyAgent/1.0",
#     # 5. 自定义头 — 某些 API 用自定义头传 API Key（如 Anthropic）
#     "x-api-key": "sk-ant-xxxxxxxxxxxxxxxxxxxx",
# }

