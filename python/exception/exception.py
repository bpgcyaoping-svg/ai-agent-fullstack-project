def read_file_config(filePath: str) -> dict:
    """
    读取配置文件内容,演示try-except-else-finally的完整用法
    """
    file = None
    try:
        file = open(filePath, "r")
        content = file.read()
        config = json.loads(content)
    except FileNotFoundError:
        # ======except:文件不存在时的备选方案
        print(f"文件 {filePath} 不存在, 无法读取配置")
        config={"model":"gpt-4", "temperature":0.5}
    except json.JSONDecodeError:
        # ======except:配置文件内容不是JSON格式时的备选方案
        print(f"配置文件 {filePath} 内容不是JSON格式, 无法解析")
        raise ValueError(f"配置文件 {filePath} 内容不是JSON格式")
    else:
        # else 一切正常
        print(f"成功读取配置文件 {filePath}")
    finally:
        if file and not file.closed:
            file.close()
            print(f"已关闭文件 {filePath}")
    return config

#  实用技巧：用 as e 获取异常对象 
from socket import timeout

import httpx
try:
    response = httpx.get("https://www.baidu.com", timeout=10)
    response.raise_for_status()
    data = response.json()
    print(data)
except httpx.TimeoutException as e:
    print(f"请求超时,详情：{e}")
    print(f"请求的URL是: {e.request.url}")  # httpx异常携带了请求对象
except httpx.HTTPStatusError as e:
    print(f"HTTP状态码错误: {e}")
    print(f"响应体: {e.response.text[:200]}")  
except httpx.RequestError as e:
    print(f"请求发送失败: {e}")


def call_tool(tool_name: str, params: dict):
    """调用一个工具"""
    handler = tool_registry.get(tool_name)
    if handler is None:
        raise ToolNotFoundError(f"工具 '{tool_name}' 不存在")  # ← 从这里抛出
    return handler(**params)                                   # ← 这里也可能抛异常


def execute_agent(query: str):
    """执行 Agent 任务"""
    try:
        tool, params = parse_intent(query)     # 可能抛 ParseError
        result = call_tool(tool, params)       # 可能抛 ToolNotFoundError / ToolExecutionError
        return result
    except ToolNotFoundError as e:
        return {"status": "error", "message": str(e)}
    # ParseError 和 ToolExecutionError 没有被捕获 → 继续往上冒泡

# def main():
#     try:
#         output = execute_agent(user_query)
#         print(output)
#     except Exception as e:                     # ← 最终兜底
#         print(f"系统错误，请稍后重试：{e}")
#         # 发送告警、写入错误日志...


# raise 主动抛异常
def divide(a, b):
    if b == 0:
        raise ValueError("除数不能为0")
    return a / b

def validate_api_response(data: dict):
    if data is None:
        raise TypeError("API返回为空")
    if "status" not in data:
        raise KeyError(f"'status'字段缺失")
    if data["status"] != "success":
        raise RuntimeError(f"API返回状态码错误: {data['status']}")

#自定义异常
class AgentError(Exception):
    """Agent执行错误基类"""
    pass
class ToolNotFoundError(AgentError):
    """工具未找到错误"""
    pass
class ToolExecutionError(AgentError):
    """工具执行错误"""
    pass

# from e
try:
    int("abc")
except ValueError as e:
    raise RuntimeError(f"转换为整数时出错: {e}") from e  


# 实用技巧：重试功能
import httpx
import time
def call_llm_api(messages: list[dict], max_retries: int = 3) -> dict:
    """
    调用LLM API, 失败自动重试, 最大重试次数为max_retries
    Args:
        messages (list[dict]): 要发送的消息列表
        max_retries (int, optional): 最大重试次数. 默认值为 3.
    Returns:
        dict: API返回的JSON数据
    Raises:
        RuntimeError: 重试耗尽后仍失败
    """ 
    for attemp in range(max_retries):
        try:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers = {"Authorrization": f"Bearer {sdk_key}"},
                json = {"model": "gtp-4","messages": messages},
                timeout = 30,
            )
            response.raise_for_status()
            return  response.json()
        except httpx.TimeoutException :
            print(f"重试{attemp+1}/{max_retries} 请求超时，等待后重试。。。")
            time.sleep(2 ** attemp)  # 每次重试之间等待2^attemp秒, 最大等待32秒
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 401:
                raise ValueError("API密钥错误")
            elif status == 429:
                raise ValueError("请求频率过快")
            elif status >= 500:
                raise ValueError("服务器错误")
            else:
                raise ValueError(f"HTTP状态码错误: {status}")   
            
    raise RuntimeError("重试耗尽后仍失败")

# ===== 使用示例 =====
try:
    result = call_llm_api([{"role": "user", "content": "你好"}])
    print(result["choices"][0]["message"]["content"])
except RuntimeError as e:
    print(f"调用失败：{e}")


import functools
import time
import random

def retry_on_failure(
    max_retries: int = 3,
    retryable_exceptions: tuple = (Exception,),
    base_delay: float = 1.0,
    max_delay: float = 30.0,
):
    """
    通用的重试装饰器。

    用法：
        @retry_on_failure(max_retries=3, retryable_exceptions=(httpx.TimeoutException,))
        def call_api(): ...

    Args:
        max_retries: 最大重试次数
        retryable_exceptions: 哪些异常可以重试（其他异常直接上抛）
        base_delay: 基础等待时间（秒）
        max_delay: 最大等待时间上限（秒）
    """
    def decorator(func):
        @functools.wraps(func)   # 保留原函数的名称和文档
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.3)
                    total = delay + jitter
                    print(f"[Retry] {func.__name__} 第 {attempt + 1} 次失败，"
                          f"{total:.1f}s 后重试（{e}）")
                    time.sleep(total)
            raise RuntimeError(
                f"{func.__name__} 重试 {max_retries} 次后仍失败。最后错误：{last_exception}"
            ) from last_exception
        return wrapper
    return decorator


# ===== 使用装饰器后的 API 调用变得极其简洁 =====
@retry_on_failure(
    max_retries=3,
    retryable_exceptions=(httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError),
)
def call_llm_api_simple(messages: list[dict]) -> dict:
    """调用 LLM API（自动重试）"""
    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": "gpt-4", "messages": messages},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
