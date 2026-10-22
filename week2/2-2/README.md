提示词:
基于 DeepSeek API的可运行 Python 代码，实现一个最小但完整的 Agent Tool Runtime	
-含 ToolDefinition、ToolRegistry、ToolSnapshot、ToolSchema、ToolRun2 
-支持注册天气查询、数据库查询两个工具，
-完成"LLMTool CallTool RuntimeHandlerTool Result7 
-包含参数校验、权限检查、风险等级、超时、错误结构化、tool_call_id保留、工具停
-代码应可直接运行，使用环境变量读取DEEPSEEK_API_KEY，9 -提供完整依赖、示例工具、主程序和最小测试。

禁止:
-不要把工具实现直接暴露给模型，
-不要跳过 Registry、Snapshot、Schema和Runtime直接调用 Handler;14
-不要省略参数校验、权限检查、结构化错误和 tool_call_id。