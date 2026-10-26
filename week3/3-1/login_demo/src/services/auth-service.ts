// 服务层：登录业务判断，成功返回令牌与脱敏后的用户信息。
import { userRepository } from "../repositories/user-repository.js";
import type { LoginResult } from "../types/user.js";
import { verifyPassword } from "../utils/password.js";
import { issueAccessToken } from "../utils/token.js";

export class AuthService {
  login(email: string, password: string): LoginResult {
    const user = userRepository.findByEmail(email);
    if (!user) {
      throw new Error("User not found");
    }
    if (!verifyPassword(password, user.passwordHash)) {
      throw new Error("Invalid password");
    }

    const accessToken = issueAccessToken({
      userId: user.id,
      role: user.role,
    });

    // 只挑选对外可见字段，避免 passwordHash 泄露到响应体。
    return {
      accessToken,
      user: {
        id: user.id,
        email: user.email,
        displayName: user.displayName,
        role: user.role,
      },
    };
  }
}

export const authService = new AuthService();
