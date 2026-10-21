from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from routers import agents_router
import time

app = FastAPI(
    title="AI Agent 管理 API",
    description="一个 CRUD 示例，管理 AI Agent 的创建、查询、更新、删除",
    version="0.1.0",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # 开发环境：允许所有来源
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求日志中间件
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start
    print(f"{request.method} {request.url.path} → {response.status_code} ({elapsed:.3f}s)")
    return response

# 挂载路由
app.include_router(agents_router)


@app.get("/health")
async def health():
    """健康检查接口（部署时用于检测服务是否存活）"""
    return {"status": "healthy"}
