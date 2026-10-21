import pytest
# test_api.py
from fastapi.testclient import TestClient
from main import app         # 导入你的 FastAPI app

client = TestClient(app)

def test_health_check():
    """测试健康检查接口"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_create_agent():
    """测试创建 Agent"""
    response = client.post(
        "/agents",
        json={
            "name": "TestAgent",
            "model": "gpt-4o",
            "temperature": 0.5,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "TestAgent"
    assert data["model"] == "gpt-4o"
    assert data["temperature"] == 0.5
    assert "id" in data

def test_get_agent_not_found():
    """测试获取不存在的 Agent"""
    response = client.get("/agents/99999")
    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]

def test_create_agent_invalid():
    """测试创建 Agent 时传入无效数据"""
    response = client.post(
        "/agents",
        json={
            "name": "",           # ← 空名称，不合法
            "temperature": 3.0,   # ← 超出范围
        },
    )
    assert response.status_code == 422   # Pydantic 校验失败
    data = response.json()
    # 检查错误详情里包含字段名
    assert "detail" in data
