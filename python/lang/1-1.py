# 变量和基本类型
name = "AI Assistant"
count = 42
price = 99.99
is_active = True
a = None

# 打印变量类型
print(type(name))      # <class 'str'>
print(type(count))     # <class 'int'>  
print(type(price))     # <class 'float'>
print(type(is_active)) # <class 'bool'>
print(type(a))         # <class 'NoneType'>

# 控制流
if count > 10:
    print("库存充足")
elif count > 5:
    print("库存一般")
else:
    print("库存不足")
    
# 循环
for i in range(1,3):
    print(i)
for x in ["a", "b", "c"]:
    print(x)

temp = 3
while temp > 0: 
    print("库存充足")
    temp -= 1

for n in range(2,10):
    for x in range(2,n):
        if n % x == 0:
            break
    else:
        print(f"{n} 是质数")
# 函数
def add(a, b):
    """
    计算两个数的和
    
    参数:
    a (int): 第一个数
    b (int): 第二个数
    
    返回:
    int: 第二个数的和
    """
    return a + b    
print(add(3,5  ))    
def greet(name, greeting="Hello"):
    """
    打印问候消息
    
    参数:
    name (str): 问候的姓名
    greeting (str, optional): 问候消息的前缀。默认值为 "Hello"。
    
    返回:
    None
    """
    print(f"{greeting}, {name}!")
greet("AI Assistant", "Hi")
def append_to_list(item, lst=None):
    """
    将一个元素添加到列表的末尾
    
    参数:
    item (any): 要添加的元素
    lst (list): 要添加元素的列表
    返回:
    None
    """
    if lst is None:
        lst = []
    lst.append(item)
    return lst
print(append_to_list(4,[1,2,3]))
def sum_all(*args):
    """
    计算多个数的和
    
    参数:
    *args (int): 任意数量的整数
    
    返回:
    int: 所有数的和
    """
    return sum(args)
print(sum_all(1,2,3,4,5))
def print_info(**kwargs):
    """
    打印关键字参数
    
    参数:
    **kwargs (dict): 任意数量的关键字参数
    
    返回:
    None
    """
    for key, value in kwargs.items():
        print(f"{key}: {value}")
print_info(name="AI Assistant", age=3, is_active=True)

squares = [x**2 for x in range(1,10)]
print(squares)

events = [i for i in range(1, 5) if i % 2 == 0]
print(events)
# ========== 字典推导式 ==========
# 构建一个工具名→工具描述映射
tool_map = {
    "search": "搜索网络",
    "read": "读取文件",
    "write": "写入文件",
}
# 调转键值对
reverse_map = {v: k for k, v in tool_map.items()}
print(reverse_map)

# 装饰器
def greet(name):
    return f"hello,{name}"
say_hello = greet;
print(say_hello("AI Assistant"))    
def call_twice(func, arg):
    return func(arg), func(arg)
print(call_twice(greet, "AI Assistant"))

from doctest import OutputChecker
import time
from unicodedata import category
# def timing_decorator(func):
#     def wrapper(*args, **kwargs):
#         start_time = time.time()
#         result = func(*args, **kwargs)
#         end_time = time.time()
#         print(f"{func.__name__} 执行时间: {end_time - start_time} 秒")
#         return result
#     return wrapper

# @timing_decorator
# def slow_function():
#     time.sleep(2)
#     return "slow_function"
# slow_function()

# 带参数的装饰器
def retry(max_attempts=3, delay=1):
    """重试装饰器 — 调用失败时自动重试"""
    import time

    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise  # 最后一次还失败，抛出异常
                    print(f"第 {attempt + 1} 次失败：{e}，{delay}秒后重试...")
                    time.sleep(delay)
        return wrapper
    return decorator

# 使用
@retry(max_attempts=3, delay=2)
def call_unstable_api():
    # ... 可能失败的 API 调用 ...
    pass

# 场景 1：记录每次工具调用的输入输出
def log_tool_call(func):
    def wrapper(*args, **kwargs):
        print(f"[Tool] 调用 {func.__name__}(args={args}, kwargs={kwargs})")
        result = func(*args, **kwargs)
        print(f"[Tool] {func.__name__} 返回 {result[:50]}...")
        return result
    return wrapper

@log_tool_call
def search_web(query: str) -> str:
    return f"搜索结果：关于 '{query}' 的 10 条相关信息..."

# 场景 2：限流保护
def rate_limit(max_calls_per_second):
    import time
    interval = 1.0 / max_calls_per_second
    last_called = [0]

    def decorator(func):
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_called[0]
            if elapsed < interval:
                time.sleep(interval - elapsed)
            result = func(*args, **kwargs)
            last_called[0] = time.time()
            return result
        return wrapper
    return decorator


# 类与面向对象
class Agent:
    """
    代理类，用于表示智能体。
    """
    category = "agent"

    def __init__(self, name, model="gpt-4"):
        self.name = name
        self.model = model
        self._memory= []
    def greet(self):
        return f"hello,{self.name}"
    def __str__(self) -> str:
        return f"{self.name}({self.model})"

class CodingAgent(Agent):
    def __init__(self, name, model="gpt-4", languages=None):
        super().__init__(name, model)
        self.languages = languages or ["python"]
    def code_review(self, code: str) -> str:
        return f"reviewing code in {self.name}: {code}"

agent = CodingAgent("AI Assistant", languages=["python", "java"])
print(agent.category)
print(agent.languages)
print(agent.code_review("print('hello world')"))

# 上下文管理器
## 文件操作with语句
# with open("example.txt", "r") as file:
#     content = file.read()
#     print(content)

class TimeerContext:
    """
    执行时间上下文管理器，用于测量代码块的执行时间。
    """

    def __init__(self):
        self.start_time = time.time()
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        end_time = time.time()
        print(f"执行时间: {end_time - self.start_time} 秒")
with TimeerContext():
    total = sum(range(1000000))

# 使用contextlib创建上下文管理器
from contextlib import contextmanager

# @contextmanager
# def temporary_env_var(key, value):
#     """
#     临时设置环境变量，执行代码块后恢复原值。
#     """
#     import os
#     old_value = os.environ.get(key)
#     os.environ[key] = value
#     try:
#         yield
#     finally:
#         if old_value is None:
#             del os.environ[key]
#         else:
#             os.environ[key] = old_value
# # 使用
# with temporary_env_var("OPEN_API_KEY", "sk-test"):
#     # ... 代码块，使用新的环境变量值 ...
#     print(os.environ["OPEN_API_KEY"])
# # 现境变量已恢复


# AI Agent开发中的典型场景
# class ToolSession:
#     def _enter__(self):
#         print("[Session]开始工具调用会话")
#         self.results = []
#         return self
#     def __exit__(self, *args):
#         print("[Session]工具调用会话结束")
#         print(f"[Session] 总调用次数: {len(self.results)}")

# with ToolSession() as session:
#     # ... 代码块，使用新的环境变量值 ...
#     session.results.append("result1")
#     session.results.append("result2")
#     session.results.append("result3")

# 类型注解基础
# 变量注解
name: str = "agent"
count: int = 44
config: dict[str, any] = {"model": "gpt-4"}

#函数注解
def greet(name:str, times:int =1)->str:
    """ 参数和返回值都有注解 """
    return f"hello, {name}!" * times
def search_web(query:str, max_results:int=10) -> list[str]:
    """ 搜索结果返回字符串列表 """
    return [f"结果{i}: 关于{query}" for i in range(max_results)]    
# 复合类型
from typing import Optional, Union,List, Dict, Tuple
#Optional可以是None
def get_user(id:int)->Optional[str]:
    """ 可能返回None """
    users = {1: "Alice", 2: "Bob"}
    return users.get(id)

#Union 多种类型之一
def process(data: Union[str, bytes]) -> str:
    """ 接受str或者bytes, 返回str类型 """    
    if isinstance(data, bytes):
        return data.decode("utf-8")
    return data
# List/Dict
tools : list[str] = ["search_web", "rate_limit"]
config: dict[str, float] = {"temperature": 0.7, "max_tokens": 1024, "top_p": 0.9}
print(config)

#tuple 元组 固定长度和类型的元组
coord: tuple[float, float] = (116.40, 39.90)


# from typing import TypeDict, Literal,Callable

# TypeDict 定义字典的类型
class ToolResult(TypeDict):
    """ 工具调用结果的类型 """
    tool_name: str
    status: Literal["success", "error"]  # 限制只能是success或error
    output: str
    duration_ms: float

# callable - 标注函数类型
ToolHandle = Callable[[str, dict], ToolResult]  #(参数类型)-> 返回类型
def register_tool(name:str, handle:ToolHandle) -> None:
    """ 注册一个工具处理器 """
    print(f"已注册工具: {name}")

# 自引用类型(用字符串延迟求值)
class TreeNode:
    def _init_(self, value:int, left:TreeNode=None, right:TreeNode=None):
        self.value = value
        self.left = left
        self.right = right