"""cancel_order 作业验收脚本：逐条验证 8 条要求。

运行方式：
    python demo_cancel_order.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "2-1"))

from pydantic import ValidationError

from execution_context import ExecutionContext
from order_schemas import ToolError
from tool_messages import ToolCall, ToolResultMessage
from tool_runtime import ToolRuntime

from cancel_order_tool import CANCEL_ORDER, CancelOrderInput, CancelOrderOutput


class CountingOrderService:
    """带调用计数的假业务服务，用来验证"拒绝路径业务调用次数为零"。"""

    def __init__(self, cancel_delay: float = 0.0) -> None:
        self.cancel_delay = cancel_delay
        self.cancel_calls = 0
        self.last_call: dict | None = None

    async def cancel(self, *, user_id: str, order_id: str, reason: str) -> str:
        self.cancel_calls += 1
        self.last_call = {
            "user_id": user_id,
            "order_id": order_id,
            "reason": reason,
        }
        print(f"    [业务取消] {self.last_call}")
        if self.cancel_delay:
            await asyncio.sleep(self.cancel_delay)
        return f"op_{order_id}_{self.cancel_calls:03d}"


def make_runtime() -> tuple[ToolRuntime, list[dict]]:
    events: list[dict] = []

    async def trace_writer(event: dict) -> None:
        events.append(event)

    runtime = ToolRuntime(trace_writer)
    runtime.register(CANCEL_ORDER)
    return runtime, events


def make_ctx(
    service: CountingOrderService,
    *,
    permissions: set[str],
    approved: frozenset[str] = frozenset(),
    trace_id: str,
) -> ExecutionContext:
    return ExecutionContext(
        user_id="user_demo",
        tenant_id="tenant_demo",
        permissions=frozenset(permissions),
        approved_call_ids=approved,
        trace_id=trace_id,
        order_service=service,
    )


async def run_call(
    runtime: ToolRuntime,
    ctx: ExecutionContext,
    call_id: str,
    arguments_json: str,
) -> ToolResultMessage:
    print(f"    模型发起调用: id={call_id}, arguments={arguments_json}")
    return await runtime.execute(
        ToolCall(id=call_id, name="cancel_order", arguments_json=arguments_json),
        ctx,
    )


def expect_error(result: ToolResultMessage, code: str) -> dict:
    assert result.is_error, f"预期失败，实际成功: {result.content}"
    error = json.loads(result.content)["error"]
    assert error["code"] == code, f"预期 {code}，实际 {error['code']}"
    return error


async def main() -> None:
    print("=== 要求 1/2/3：Schema 定义 ===")
    tool_json = CANCEL_ORDER.to_model_tool()
    print(json.dumps(tool_json, ensure_ascii=False, indent=2))
    params = tool_json["function"]["parameters"]
    # 要求 1：只允许 order_id 和 reason，禁止额外字段
    assert set(params["properties"]) == {"order_id", "reason"}
    assert set(params["required"]) == {"order_id", "reason"}
    assert params["additionalProperties"] is False
    # 要求 5：用户身份不在模型可见的 Schema 里
    assert "user_id" not in params["properties"]
    print("Input Schema 只含 order_id/reason 且 additionalProperties=false：OK")
    # 要求 2：Output Schema 三个字段
    output = CancelOrderOutput(order_id="ord_x", status="cancelled", operation_id="op_1")
    assert set(output.model_dump()) == {"order_id", "status", "operation_id"}
    print("Output Schema 返回 order_id/status/operation_id：OK")
    # 要求 3：Error Schema 复用本节 ToolError
    assert CANCEL_ORDER.error_model is ToolError
    print("error_model 复用 2-1 的 ToolError：OK")

    # 模型试图在参数里多塞一个 user_id，必须被拒绝
    try:
        CancelOrderInput.model_validate_json(
            '{"order_id": "ord_1001", "reason": "不想要了", "user_id": "attacker"}'
        )
        raise AssertionError("额外字段 user_id 未被拒绝")
    except ValidationError:
        print("模型注入 user_id 被拒绝：OK")

    print("\n=== 要求 4：权限 / 风险 / 幂等 ===")
    assert CANCEL_ORDER.permission == "order:write"
    assert CANCEL_ORDER.risk == "high"
    assert CANCEL_ORDER.idempotent is False
    assert CANCEL_ORDER.max_retries == 0
    print("permission=order:write, risk=high, idempotent=False, max_retries=0：OK")

    runtime, events = make_runtime()

    print("\n=== 要求 5/8：成功路径（身份来自 ctx，trace 可定位）===")
    service = CountingOrderService()
    ctx = make_ctx(
        service,
        permissions={"order:read", "order:write"},
        approved=frozenset({"call_ok"}),
        trace_id="trace_cancel_success",
    )
    result = await run_call(
        runtime,
        ctx,
        "call_ok",
        '{"order_id": "ord_1001", "reason": "用户改变主意，不想要了"}',
    )
    assert not result.is_error, result.content
    output = CancelOrderOutput.model_validate_json(result.content)
    print(f"    Output: {output.model_dump()}")
    assert output.order_id == "ord_1001"
    assert output.status == "cancelled"
    assert output.operation_id.startswith("op_")
    assert service.cancel_calls == 1
    # 身份来自 ExecutionContext：业务服务收到的 user_id 与 ctx 一致，而不是模型参数
    assert service.last_call["user_id"] == ctx.user_id == "user_demo"
    print("成功执行，业务收到的 user_id 来自 ctx：OK")

    print("\n=== 要求 6a：非法参数 → 业务调用次数为 0 ===")
    service = CountingOrderService()
    ctx = make_ctx(
        service,
        permissions={"order:read", "order:write"},
        trace_id="trace_cancel_invalid",
    )
    result = await run_call(runtime, ctx, "call_bad", '{"order_id": ""}')
    expect_error(result, "INVALID_ARGUMENT")
    # 模型注入额外字段，同样在参数校验阶段被拒绝
    result = await run_call(
        runtime,
        ctx,
        "call_bad_extra",
        '{"order_id": "ord_1001", "reason": "r", "user_id": "attacker"}',
    )
    expect_error(result, "INVALID_ARGUMENT")
    assert service.cancel_calls == 0
    print("非法参数未触达业务函数（cancel_calls=0）：OK")

    print("\n=== 要求 6b：无权限 → 业务调用次数为 0 ===")
    service = CountingOrderService()
    ctx = make_ctx(service, permissions={"order:read"}, trace_id="trace_cancel_no_perm")
    visible = [tool["function"]["name"] for tool in runtime.model_tools(ctx)]
    print(f"    只读身份可见工具: {visible}")
    assert "cancel_order" not in visible  # 无权限时模型根本看不到该工具
    result = await run_call(
        runtime, ctx, "call_noperm", '{"order_id": "ord_1001", "reason": "test"}'
    )
    expect_error(result, "PERMISSION_DENIED")
    assert service.cancel_calls == 0
    print("无权限未触达业务函数（cancel_calls=0）：OK")

    print("\n=== 要求 6c：高风险未确认 → 业务调用次数为 0 ===")
    service = CountingOrderService()
    ctx = make_ctx(
        service,
        permissions={"order:read", "order:write"},
        approved=frozenset(),  # 用户尚未确认这次调用
        trace_id="trace_cancel_unapproved",
    )
    result = await run_call(
        runtime, ctx, "call_unapproved", '{"order_id": "ord_1001", "reason": "test"}'
    )
    expect_error(result, "APPROVAL_REQUIRED")
    assert service.cancel_calls == 0
    print("未确认未触达业务函数（cancel_calls=0）：OK")

    print("\n=== 要求 7：超时后不自动重试取消动作 ===")
    service = CountingOrderService(cancel_delay=5.0)  # 业务耗时超过 timeout_seconds
    ctx = make_ctx(
        service,
        permissions={"order:read", "order:write"},
        approved=frozenset({"call_slow"}),
        trace_id="trace_cancel_timeout",
    )
    result = await run_call(
        runtime, ctx, "call_slow", '{"order_id": "ord_1001", "reason": "演示超时"}'
    )
    error = expect_error(result, "TIMEOUT")
    assert error["retryable"] is False
    assert service.cancel_calls == 1  # 只执行了一次，超时后没有自动重试
    print("超时后未自动重试（业务仍只被调用 1 次）：OK")

    print("\n=== 要求 8：成功 / 拒绝 / 失败 均可用 trace_id + tool_call_id 定位 ===")
    for event in events:
        assert event["trace_id"], "trace 事件缺少 trace_id"
        assert event["tool_call_id"], "trace 事件缺少 tool_call_id"
    print(json.dumps(events, ensure_ascii=False, indent=2))
    outcomes = {(event["status"], event["error_code"]) for event in events}
    assert ("success", None) in outcomes
    assert ("error", "INVALID_ARGUMENT") in outcomes
    assert ("error", "PERMISSION_DENIED") in outcomes
    assert ("error", "APPROVAL_REQUIRED") in outcomes
    assert ("error", "TIMEOUT") in outcomes
    print("成功、拒绝、失败均有 trace 记录：OK")

    print("\n全部 8 条要求验收通过")


if __name__ == "__main__":
    asyncio.run(main())
