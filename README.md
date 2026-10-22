业务 Agent ──POST /v1/llm──▶ FastAPI 入口校验 (Pydantic)
                                  │
                          render_prompt (模板注入)
                                  │
                          validate_model (白名单)
                                  │
                    OpenAICompatibleProvider (持有密钥)
                          ↙ 重试1次 ↘ fallback
                     主模型 ──────▶ 备用模型
                                  │
                    record_trace (成本/延迟/状态审计)
                                  │
                      统一响应 / SSE 流 ──▶ 业务 Agent
