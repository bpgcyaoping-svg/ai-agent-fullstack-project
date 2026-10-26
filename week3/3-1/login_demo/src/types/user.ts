// 用户与登录相关类型定义：仓储层、服务层、路由层共用同一份契约。
export type UserRole = "admin" | "member";

export interface UserRecord {
  id: string;
  email: string;
  passwordHash: string;
  displayName: string;
  role: UserRole;
}

export interface LoginResult {
  accessToken: string;
  user: Pick<UserRecord, "id" | "email" | "displayName" | "role">;
}

export interface AuthTokenPayload {
  userId: string;
  role: UserRole;
}
