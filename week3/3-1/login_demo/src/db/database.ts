// Demo 级内存数据库：不做持久化，进程重启即重置，仅用于演示查询链路。
import type { UserRecord } from "../types/user.js";

export type TableName = "users";

const usersTable: UserRecord[] = [
  {
    id: "u_1001",
    email: "demo@example.com",
    passwordHash: "hash:demo123",
    displayName: "Demo User",
    role: "member",
  },
  {
    id: "u_1002",
    email: "admin@example.com",
    passwordHash: "hash:admin123",
    displayName: "Admin User",
    role: "admin",
  },
];

export function getTable(table: TableName): UserRecord[] {
  switch (table) {
    case "users":
      return usersTable;
    default: {
      const unreachable: never = table;
      throw new Error(`Unknown table: ${String(unreachable)}`);
    }
  }
}

// 返回第一条满足 where 全部键值相等的记录，找不到返回 undefined。
export function queryOne(
  table: TableName,
  where: Partial<UserRecord>,
): UserRecord | undefined {
  const rows = getTable(table);
  return rows.find((row) =>
    (Object.keys(where) as (keyof UserRecord)[]).every(
      (key) => row[key] === where[key],
    ),
  );
}
