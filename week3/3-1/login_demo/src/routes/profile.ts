// 受保护路由：先经过 authMiddleware，再返回当前登录用户载荷。
import { Router } from "express";

import { authMiddleware } from "../middleware/auth-middleware.js";

export const profileRouter = Router();

profileRouter.get("/profile", authMiddleware, (_request, response) => {
  response.status(200).json({
    message: "Authenticated request",
    currentUser: response.locals.currentUser,
  });
});
