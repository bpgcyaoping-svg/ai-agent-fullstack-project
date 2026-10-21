# 基础练习
# 1.定义一个温度转换器
def celsius_to_fahrenheit(c: float) -> float:
    """ 将摄氏度转换为华氏度 """
    return c * 9 / 5 + 32

def fahrenheit_to_celsius(f: float) -> float:
    """ 将华氏度转换为摄氏度 """
    return (f - 32) * 5 / 9

# 2.质数判断器  写一个函数 is_prime(n: int) -> bool，判断一个数是否为质数。用 for-else 语法实现。
def is_prime(n: int) -> bool:
    """ 判断一个数是否为质数 """
    if n <= 1:
        return False
    for x in range(2,n):
        if n % x == 0:
            return False
    return True
# 3.学生管理系统： 定义一个 Student 类，包含属性 name、scores（科目→分数的字典），方法 average_score() -> float、best_subject() -> str 
class Student:
    def __init__(self, name: str, scores: dict):
        self.name = name
        self.scores = scores
    def average_score(self) -> float:
        """ 计算学生的平均成绩 """
        if not self.scores:
            return 0
        return sum(self.scores.values()) / len(self.scores)
    def best_subject(self) -> str:
        """ 返回学生的最佳科目 """
        if not self.scores:
            return ""
        return max(self.scores, key=self.scores.get)

# 进阶练习
# 4.装饰器实战： 写一个 @cache_result 装饰器，缓存函数的计算结果。相同的输入参数不再重新计算，直接返回缓存值。
def cache_result(func):
    cache = {}
    def wrapper(*args, **kwargs):
        if args not in cache:
            cache[args] = func(*args, **kwargs)
        return cache[args]
    return wrapper
def is_prime(n: int) -> bool:
    """ 判断一个数是否为质数 """
    if n <= 1:
        return False
    for x in range(2,n):
        if n % x == 0:
            return False
    return True
@cache_result
def is_prime_cached(n: int) -> bool:
    """ 判断一个数是否为质数 """
    return is_prime(n)

# 5.推导式转换： 将以下 for 循环改写成推导式：
 # 原始代码
result = []
for student in students:
    if student.average_score() >= 60:
        result.append(student.name)
# 改写后的推导式
result = [student.name for student in students if student.average_score() >= 60]

# 6.上下文管理器实战： 写一个 FileBackup 上下文管理器，进入时自动备份指定文件（加 .bak 后缀），退出时打印备份文件路径
import os
import shutil
from contextlib import contextmanager

@contextmanager
def FileBackup(filename: str):
    """ 上下文管理器，自动备份指定文件（加 .bak 后缀），退出时打印备份文件路径 """
    backup_filename = filename + ".bak"
    try:
        shutil.copy(filename, backup_filename)
        yield backup_filename
    finally:
        print(f"备份文件已创建：{backup_filename}")
      
# 综合练习
# 7.工具注册表： 用字典推导式 + 装饰器实现一个工具注册表：
tool_registry = {}

# 实现 register_tool 装饰器
def register_tool(tool_name: str):
    def wrapper(func):
        tool_registry[tool_name] = func
        return func
    return wrapper

@register_tool("search_web")  
def search_web(query: str) -> list[str]:
     """搜索网络"""
     return [f"结果: {query}"]