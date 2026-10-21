-- ===== 建表 =====
CREATE TABLE agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,       -- 自增主键
    name TEXT NOT NULL UNIQUE,                   -- 名字不能为空、不能重复
    model TEXT DEFAULT 'gpt-4o',                  -- 默认值
    system_prompt TEXT,                          -- 可以为空
    max_tokens INTEGER DEFAULT 4096,
    temperature REAL DEFAULT 0.7,                -- REAL = 浮点数
    is_active BOOLEAN DEFAULT 1,                 -- 布尔值（0/1）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ===== 增删改查（CRUD）=====
-- Create（增）
INSERT INTO agents (name, model, system_prompt) VALUES ('CodeBuddy', 'gpt-4o', '你是一个代码助手');

-- Read（查）
SELECT * FROM agents;                                          -- 全部
SELECT name, model FROM agents WHERE is_active = 1;            -- 条件过滤
SELECT * FROM agents WHERE name LIKE '%code%';                 -- 模糊搜索
SELECT * FROM agents ORDER BY created_at DESC LIMIT 10;        -- 排序 + 分页

-- Update（改）
UPDATE agents SET temperature = 0.8 WHERE name = 'CodeBuddy';

-- Delete（删）
DELETE FROM agents WHERE id = 3;

-- ===== 进阶 =====
-- 聚合统计：每个模型有多少 Agent
SELECT model, COUNT(*) as count FROM agents GROUP BY model;

-- 关联查询：查 Agent 及其工具（假设有 tools 表）
SELECT a.name, t.name as tool_name
FROM agents a
JOIN agent_tools at ON a.id = at.agent_id
JOIN tools t ON at.tool_id = t.id;
