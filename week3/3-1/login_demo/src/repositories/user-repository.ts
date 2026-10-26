// 仓储层：只负责按条件查用户，不包含业务判断。
import { queryOne } from "../db/database.js";
import type { UserRecord } from "../types/user.js";

export class UserRepository {
  findByEmail(email: string): UserRecord | undefined {
    return queryOne("users", { email });
  }
}

export const userRepository = new UserRepository();
