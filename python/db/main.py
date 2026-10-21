from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from database import engine, get_db, Base
from models import Agent
from schemas import AgentCreate, AgentUpdate, AgentResponse

# 创建表
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Agent 管理 API")

# ===== CRUD 接口 =====

@app.post("/agents", response_model=AgentResponse, status_code=201)
def create_agent(data: AgentCreate, db: Session = Depends(get_db)):
    """创建 Agent"""
    # 检查同名
    existing = db.query(Agent).filter(Agent.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Agent '{data.name}' 已存在")

    agent = Agent(**data.model_dump())
    db.add(agent)
    db.commit()
    db.refresh(agent)  # 获取数据库生成的 id 和 created_at
    return agent


@app.get("/agents", response_model=list[AgentResponse])
def list_agents(
    skip: int = 0,
    limit: int = 20,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    """获取 Agent 列表（支持分页和过滤）"""
    query = db.query(Agent)
    if is_active is not None:
        query = query.filter(Agent.is_active == is_active)
    return query.order_by(Agent.created_at.desc()).offset(skip).limit(limit).all()


@app.get("/agents/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    """获取单个 Agent"""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return agent


@app.patch("/agents/{agent_id}", response_model=AgentResponse)
def update_agent(agent_id: int, data: AgentUpdate, db: Session = Depends(get_db)):
    """部分更新 Agent"""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    # 只更新非空字段
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(agent, key, value)

    db.commit()
    db.refresh(agent)
    return agent


@app.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: int, db: Session = Depends(get_db)):
    """删除 Agent"""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    db.delete(agent)
    db.commit()


@app.get("/health")
def health():
    return {"status": "ok"}
