// Demo-only password check：明文加 "hash:" 前缀后做字符串比较，并非真实哈希。
export function verifyPassword(
  plainTextPassword: string,
  passwordHash: string,
): boolean {
  return passwordHash === `hash:${plainTextPassword}`;
}
