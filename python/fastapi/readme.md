agent_api/
├── main.py           # 入口文件：创建 app、挂载路由、配置中间件
├── models.py         # Pydantic 数据模型
├── routers/
│   ├── __init__.py
│   └── agents.py     # Agent CRUD 路由
├── dependencies.py   # 共享依赖（认证、数据库等）
└── db.py             # 模拟数据库（正式课会替换为真实 DB）
