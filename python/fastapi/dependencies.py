"""共享的依赖项"""
from fastapi import Header, HTTPException, Depends
from db import agent_db


async def verify_api_key(x_api_key: str = Header(..., description="API 密钥")):
    """验证 API Key —— 简化版，正式课会换成 JWT"""
    valid_keys = {"sk-test-key-123"}
    if x_api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    return x_api_key


def get_db():
    """获取数据库实例（当前是模拟 DB，正式课用 Session）"""
    return agent_db
