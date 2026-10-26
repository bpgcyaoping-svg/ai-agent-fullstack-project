// Demo 级令牌：只做 base64url 编码，未加密、未签名，禁止用于生产。
import type { AuthTokenPayload } from "../types/user.js";

const TOKEN_PREFIX = "demo-token.";

export function issueAccessToken(payload: AuthTokenPayload): string {
  const encoded = Buffer.from(JSON.stringify(payload), "utf8").toString("base64url");
  return `${TOKEN_PREFIX}${encoded}`;
}

export function parseAccessToken(token: string): AuthTokenPayload | null {
  if (!token.startsWith(TOKEN_PREFIX)) {
    return null;
  }
  try {
    const decoded = Buffer.from(
      token.slice(TOKEN_PREFIX.length),
      "base64url",
    ).toString("utf8");
    return JSON.parse(decoded) as AuthTokenPayload;
  } catch {
    return null;
  }
}
