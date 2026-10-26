// 应用装配：挂载中间件、登录路由与受保护路由。
import express, { type Express } from "express";

import { loginRouter } from "./routes/login.js";
import { profileRouter } from "./routes/profile.js";

export function createApp(): Express {
  const app = express();

  // 解析 JSON 请求体，是 request.body 可用的前提。
  app.use(express.json());
  app.use("/api", loginRouter);
  app.use("/api", profileRouter);

  app.get("/health", (_request, response) => {
    response.status(200).json({ status: "ok" });
  });

  return app;
}
