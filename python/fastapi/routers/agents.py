from fastapi import APIRouter, HTTPException, Depends, Query
from models import (
    CreateAgentRequest,
    UpdateAgentRequest,
    AgentResponse,
    AgentListResponse,
)
from dependencies import verify_api_key, get_db

router = APIRouter(prefix="/agents", tags=["Agents"])


@router.post("/", response_model=AgentResponse, status_code=201)
async def create_agent(
    request: CreateAgentRequest,
    # api_key: str = Depends(verify_api_key),   # ← 实际项目中取消注释
    db=Depends(get_db),
):
    """创建一个新的 Agent"""
    return db.create(request.model_dump())


@router.get("/", response_model=AgentListResponse)
async def list_agents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    search: str = Query(default=""),
    db=Depends(get_db),
):
    """获取 Agent 列表（支持分页、筛选、搜索）"""
    all_agents = db.list_all()

    # 筛选
    if status:
        all_agents = [a for a in all_agents if a.get("status") == status]
    if search:
        all_agents = [
            a for a in all_agents
            if search.lower() in a.get("name", "").lower()
            or search.lower() in a.get("description", "").lower()
        ]

    # 分页
    total = len(all_agents)
    start = (page - 1) * page_size
    end = start + page_size
    items = all_agents[start:end]

    return AgentListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: int, db=Depends(get_db)):
    """获取单个 Agent"""
    agent = db.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")
    return agent


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: int,
    request: UpdateAgentRequest,
    db=Depends(get_db),
):
    """部分更新 Agent —— 只更新传入的字段"""
    agent = db.update(agent_id, request.model_dump(exclude_unset=True))
    # exclude_unset=True → 只序列化"用户传了的"字段，不传的不会被覆盖
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")
    return agent


@router.delete("/{agent_id}")
async def delete_agent(agent_id: int, db=Depends(get_db)):
    """删除 Agent"""
    if not db.delete(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")
    return {"ok": True, "message": f"Agent {agent_id} 已删除"}
