// 鉴权中间件：校验 Bearer 令牌，成功时把载荷写入 response.locals。
import type { NextFunction, Request, Response } from "express";

import { parseAccessToken } from "../utils/token.js";

const BEARER_PREFIX = "Bearer ";

export function authMiddleware(
  request: Request,
  response: Response,
  next: NextFunction,
): void {
  const authorization = request.headers.authorization;
  if (!authorization || !authorization.startsWith(BEARER_PREFIX)) {
    response.status(401).json({ error: "Missing bearer token" });
    return;
  }

  const payload = parseAccessToken(authorization.slice(BEARER_PREFIX.length));
  if (!payload) {
    response.status(401).json({ error: "Invalid token" });
    return;
  }

  response.locals.currentUser = payload;
  next();
}
