// 路由层：只处理 HTTP 出入参，把业务判断委托给 authService。
import { Router } from "express";

import { authService } from "../services/auth-service.js";

export const loginRouter = Router();

loginRouter.post("/login", async (request, response) => {
  const { email, password } = request.body as {
    email?: string;
    password?: string;
  };

  if (!email || !password) {
    response.status(400).json({ error: "email and password are required" });
    return;
  }

  try {
    const result = authService.login(email, password);
    response.status(200).json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Login failed";
    response.status(401).json({ error: message });
  }
});
