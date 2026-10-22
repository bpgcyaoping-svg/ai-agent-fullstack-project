"""作业：为「订单运营Agent」补充 cancel_order 工具。

设计要点（对应作业 8 条要求）：
- Input Schema 只暴露 order_id / reason，extra="forbid" 禁止额外字段；
- Output Schema 返回 order_id、取消后的状态、业务操作编号；
- Error Schema 复用 2-1 的 ToolError；
- 用户身份 user_id 来自 ExecutionContext（运行时注入），模型不可见、不可填；
- 高风险调用的审批记录由运行时持有（ctx.approved_call_ids），模型无法伪造；
- 非幂等：超时/失败一律不自动重试，避免重复取消。
"""

import sys
from pathlib import Path
from typing import Literal

# 复用 2-1 小节的框架代码（Schema / ToolDefinition / ExecutionContext）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "2-1"))

from pydantic import Field

from execution_context import ExecutionContext
from order_schemas import StrictModel, ToolError
from tool_definition import ToolDefinition


class CancelOrderInput(StrictModel):
    """模型唯一能填写的部分：订单号 + 取消原因，禁止额外字段。"""

    order_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=200)


class CancelOrderOutput(StrictModel):
    """业务函数的返回值，经 Output Schema 验收后才写回模型。"""

    order_id: str
    status: Literal["cancelled"]  # 取消后的状态
    operation_id: str  # 业务操作编号


async def cancel_order(
    args: CancelOrderInput,
    ctx: ExecutionContext,
) -> CancelOrderOutput:
    # 身份取自运行时上下文 ctx.user_id，而不是模型参数 —— 模型无法伪造调用者
    operation_id = await ctx.order_service.cancel(
        user_id=ctx.user_id,
        order_id=args.order_id,
        reason=args.reason,
    )
    return CancelOrderOutput(
        order_id=args.order_id,
        status="cancelled",
        operation_id=operation_id,
    )


CANCEL_ORDER = ToolDefinition(
    name="cancel_order",
    description=(
        "按订单号取消当前用户的订单，必须说明取消原因。"
        "取消动作不可撤销；高风险操作，需用户确认后才会执行。"
    ),
    input_model=CancelOrderInput,
    output_model=CancelOrderOutput,
    error_model=ToolError,  # 复用 2-1 的统一错误协议
    permission="order:write",  # 需要写权限
    risk="high",  # 高风险：runtime 强制要求用户确认
    timeout_seconds=1.0,  # 演示用较短超时
    max_retries=0,  # 非幂等：不允许自动重试
    idempotent=False,  # 重复执行会重复取消，runtime 据此关闭重试
    handler=cancel_order,
)
