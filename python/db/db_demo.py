from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import DeclarativeBase, Session, Mapped, mapped_column
from datetime import datetime

# ===== 1. 连接数据库 =====
# SQLite（文件）
engine = create_engine("sqlite:///agent.db", echo=False)  # echo=True 可以看到生成的 SQL

# PostgreSQL（TCP 连接）
# engine = create_engine("postgresql+psycopg2://user:pass@localhost:5432/agent_dev")

# ===== 2. 定义模型 =====
class Base(DeclarativeBase):
    pass

class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(50), default="gpt-4o")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Agent(id={self.id}, name='{self.name}', model='{self.model}')>"

# ===== 3. 创建表 =====
Base.metadata.create_all(engine)  # 自动创建所有定义的表

# ===== 4. CRUD 操作 =====
with Session(engine) as session:
    # Create — 创建
    agent = Agent(
        name="CodeBuddy",
        model="gpt-4o",
        system_prompt="你是一个专业的代码助手，擅长 Python 和 TypeScript。",
    )
    session.add(agent)
    session.commit()  # 提交事务
    print(f"创建成功，ID={agent.id}")

    # Read — 查询
    agents = session.query(Agent).filter(Agent.is_active == True).all()
    for a in agents:
        print(f"  {a.name} — {a.model}")

    # 查单个
    agent = session.query(Agent).filter(Agent.name == "CodeBuddy").first()
    if agent:
        print(f"找到: {agent}")

    # Update — 更新
    agent.temperature = 0.9
    session.commit()  # SQLAlchemy 自动跟踪修改

    # Delete — 删除
    # session.delete(agent)
    # session.commit()
