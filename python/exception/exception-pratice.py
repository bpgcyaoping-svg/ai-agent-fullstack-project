# 基础练习
# 1. 安全除法 写一个函数 safe_divide(a, b)，返回除法结果。如果除数为零，返回 None；如果参数类型不对，打印错误信息并返回 None。
from zoneinfo import available_timezones


def safe_divide(a, b):
    if b == 0:
        return None
    try:
        return a / b
    except ValueError as e:
        print(f"参数错误: {e}")
        return None
    except ZeroDivisionError as e:
        print(f"除法错误: {e}")
        return None
# 2.JSON安全解析 写一个函数 parse_config(json_str: str) -> dict。如果 JSON 格式错误，打印错误信息并返回空字典 {}。用 try/except/else 结构：只有解析成功时才打印 “配置加载成功”。
def parse_config(json_str: str) -> dict:
    """
    安全解析JSON字符串,返回解析后的字典
    """
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        return {}
    else:
        print("配置加载成功")
        return {}
# 3.文件按行读取 写一个函数 read_lines_safe(filepath: str) -> list[str]，读取文件的每一行。文件不存在时返回空列表；读取过程中发生任何错误时，确保文件被关闭。
def read_lines_safe(filepath: str) -> list[str]:
    """
    安全读取文件每一行,返回文件内容的列表
    """
    try:
        with open(filepath, "r") as file:
            lines = file.readlines()
    except FileNotFoundError as e:
        print(f"文件 {filepath} 不存在, 无法读取")
        return []
    except Exception as e:
        print(f"读取文件 {filepath} 时发生错误: {e}")
        return []
    else:
        print(f"成功读取文件 {filepath}")
        return lines
    finally:
        if file and not file.closed:
            file.close()
            print(f"已关闭文件 {filepath}")
        return []


# 进阶练习 自定义异常+异常链，定义 APIError 异常类，包含 status_code 和 response_body 属性。然后写一个函数： 调用一个模拟的 API（你可以用 raise 来模拟 API 错误），如果 API 返回非 2xx，抛出自定义的 APIError，用 from e 保留原始异常链。
class APIError(Exception):
    """API错误基类"""
    def __init__(self, status_code: int, response_body: str):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"API错误: {status_code} - {response_body}")

def call_api(url: str, method: str, data: dict) -> dict:
    """
    调用模拟的 API, 失败自动重试, 最大重试次数为max_retries
    Args:
        url (str): API的URL
        method (str): 请求方法, 例如 "POST" 或 "GET"
        data (dict): 请求体数据
    Returns:
        dict: API返回的JSON数据
    Raises:
        APIError: API返回非2xx状态码
    """
    try:
        response = httpx.post(
            url,
            headers = {"Authorrization": f"Bearer {sdk_key}"},
            json = data,
            timeout = 30,
        )
        response.raise_for_status()
        return  response.json()
    except httpx.TimeoutException :
        print(f"重试{attemp+1}/{max_retries} 请求超时，等待后重试。。。")
        time.sleep(2 ** attemp)  # 每次重试之间等待2^attemp秒, 最大等待32秒
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status >= 200:
            raise APIError(status, response.text) from e
        else:
            raise RuntimeError(status, response.text) from e

# 5.多层异常处理.模拟一个 “Agent → Tool → HTTP” 三层调用栈。在 HTTP 层抛出 ConnectionError，在 Tool 层用 RuntimeError 包装它（带 from e），在 Agent 层分别捕获 RuntimeError 和 ToolNotFoundError，输出不同的提示信息。
class ToolNotFoundError(Exception):
    """工具未找到错误"""
    pass
# HTTP 层：只负责发请求，遇到网络问题抛 ConnectionError
def http_request(url: str) -> dict:
    """
    模拟一次 HTTP 请求。
    这里故意抛 ConnectionError 来模拟网络断开。
    """
    print(f"发送请求到 {url}")
    raise ConnectionError(f"网络断开,无法连接到: {url}")

# Tool 层：调用 HTTP，遇到底层异常时用 RuntimeError 包装
def tool_call(tool_name: str, url: str) -> dict:
    """
    根据工具名调用对应的 HTTP 接口。
    - 工具不存在 -> ToolNotFoundError
    - HTTP 失败   -> RuntimeError（用 from e 保留原始异常链）
    """
    available_tools = {"search", "weather"}
    if tool_name not in available_tools:
        raise ToolNotFoundError(f"工具未找到: {tool_name}")
    try:
        return http_request(url)
    except ConnectionError as e:
        raise RuntimeError(f"HTTP请求失败: {e}") from e

# Agent 层：分层捕获不同异常，输出不同提示
def agent_call(task: str):
    """
    模拟 Agent 调度工具完成任务。
    """
    print(f"开始处理任务: {task}")
    # 简单把任务映射到工具和url
    tool_map = {
        "查天气": ("weather","https://api.example.com/weather"),
        "搜索": ("search","https://api.example.com/search"),
        "计算": ("calculate","https://api.example.com/calculate"), # 工具不存在
    }
    if task not in tool_map:
        print(f"任务未找到: {task}")
        raise ToolNotFoundError(f"任务未找到: {task}")

    tool_name, url = tool_map[task]
    try:
        return tool_call(tool_name,url)
    except ToolNotFoundError as e:
        print(f"工具未找到: {e}")
        return {"error": str(e)}
    except RuntimeError as e:
        print(f"HTTP请求失败: {e}")
        return {"error": str(e)}
    else:
        return {"success": "工具调用成功"}  

if __name__ == "__main__":
    agent_call("查天气")   # 触发 ConnectionError -> 被包装成 RuntimeError
    agent_call("画画")     # 触发 ToolNotFoundError


# 6.6. 完整的 API 调用函数（带重试 + 降级）,写一个 call_llm_with_fallback(messages: list[dict], primary_model: str, fallback_model: str) -> str 函数：先尝试调用主模型（primary_model），最多重试 2 次,如果主模型失败，自动降级到备用模型（fallback_model），备用模型也最多重试 1 次,如果两个模型都失败，返回一个友好的错误消息字符串（而不是抛异常让服务崩溃）
#区分以下场景并给出不同日志：超时 → “模型 X 响应超时”,限流（429） → “模型 X 被限流，等待 N 秒”,其他错误 → “模型 X 未知错误：{详情}”,提示：你可以用装饰器或手动写重试循环。关键是要让主模型和备用模型形成双层保护。
import time 
import httpx
import logging
 # 配置日志
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 常量
API_URL = "https://api.openai.com/v1/chat/completions"
API_KEY = "sk-your-key"          # 实际使用时从环境变量读取
TIMEOUT = 30

# 底层：单次调用
def _call_once(model: str, messages: list[dict]) -> dict:
    """
    调用一次模型, 返回回复文本
    抛出的异常：
    = httpx.TimeoutException ->超时
    = httpx.HTTPStatusError  -> 4xx / 5xx
    = httpx.RequestError    -> 网络层错误
    """
    try:
        response = httpx.post(
            API_URL,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": model, "messages": messages},
            timeout=TIMEOUT
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException as e:
        logger.error(f"模型 {model} 响应超时")
        raise httpx.TimeoutException(f"模型 {model} 响应超时") from e
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status >= 200:
            raise APIError(status, response.text) from e
        else:
            raise RuntimeError(status, response.text) from e
    except httpx.RequestError as e:
        logger.error(f"模型 {model} 请求失败: {e}")
        raise httpx.RequestError(f"模型 {model} 请求失败: {e}") from e

def _call_with_retry(model: str, messages: list[dict], max_retries: int = 2) -> str | None:
    """
    调用模型, 最多重试 max_retries 次
    抛出的异常：
    = httpx.TimeoutException ->超时
    = httpx.HTTPStatusError  -> 4xx / 5xx
    = httpx.RequestError    -> 网络层错误
    """
    for attempt in range(max_retries):
        try:
            response = _call_once(model, messages)
            return response.choices[0].message.content
        except httpx.TimeoutException:
            logger.warning(f"模型 {model} 响应超时（第 {attempt + 1} 次）")

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                wait = 2 ** attempt          # 指数退避：1s, 2s, 4s
                logger.warning(f"模型 {model} 被限流，等待 {wait} 秒")
                time.sleep(wait)
            elif 400 <= status < 500:
                # 客户端错误（400/401/403/404），重试无意义，直接放弃
                logger.error(f"模型 {model} 客户端错误 HTTP {status}，不再重试")
                return None
            else:  # 5xx
                logger.warning(f"模型 {model} 服务端错误 HTTP {status}")

        except httpx.RequestError as e:
            logger.warning(f"模型 {model} 网络错误：{e}")

        except Exception as e:
            logger.error(f"模型 {model} 未知错误：{e!r}")
            return None

        # 最后一次失败后不再 sleep
        if attempt < max_retries:
            time.sleep(2 ** attempt)
    return None


# 顶层：主备降级 + 友好返回
def call_llm_with_fallback(messages: list[dict], primary_model: str, fallback_model: str) -> str | None:
    """
    调用主模型（primary_model），最多重试 2 次,如果主模型失败，自动降级到备用模型（fallback_model），备用模型也最多重试 1 次,如果两个模型都失败，返回一个友好的错误消息字符串（而不是抛异常让服务崩溃）
    """
    primary_response = _call_with_retry(primary_model, messages)
    if primary_response is not None:
        logger.info(f"主模型 {primary_model} 调用成功: {primary_response}")
        return primary_response
    fallback_response = _call_with_retry(fallback_model, messages, max_retries=1)
    if fallback_response is not None:
        logger.info(f"备用模型 {fallback_model} 调用成功: {fallback_response}")
        return fallback_response
    logger.error(f"主模型 {primary_model} 和备用模型 {fallback_model} 都调用失败")
    return f"主模型 {primary_model} 和备用模型 {fallback_model} 都调用失败"

# ============================================================
# 使用示例
# ============================================================
if __name__ == "__main__":
    reply = call_llm_with_fallback(
        messages=[{"role": "user", "content": "你好"}],
        primary_model="gpt-4",
        fallback_model="gpt-3.5-turbo",
    )
    print("最终返回：", reply)    