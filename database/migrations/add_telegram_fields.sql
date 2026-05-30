-- 添加 Telegram 推送相关字段
-- 执行时间：2026-05-30

-- 1. 添加新字段
ALTER TABLE subscriptions
    ADD COLUMN chat_id BIGINT DEFAULT NULL AFTER email,
    ADD COLUMN push_time VARCHAR(8) DEFAULT '09:00:00' AFTER frequency,
    ADD COLUMN timezone VARCHAR(50) DEFAULT 'UTC' AFTER push_time,
    ADD COLUMN push_channel ENUM('telegram', 'email') DEFAULT 'telegram' AFTER timezone,
    ADD COLUMN last_pushed_at DATETIME DEFAULT NULL AFTER confirmed_at;

-- 2. 添加索引
ALTER TABLE subscriptions
    ADD INDEX idx_chat_id (chat_id),
    ADD INDEX idx_push_time (push_time);

-- 3. 允许 email 为 NULL（Telegram 订阅无需邮箱）
ALTER TABLE subscriptions MODIFY COLUMN email VARCHAR(255) DEFAULT NULL;

-- 4. 移除 email 唯一约束，改为 chat_id 唯一约束
ALTER TABLE subscriptions DROP INDEX email;
ALTER TABLE subscriptions ADD UNIQUE INDEX idx_chat_id_unique (chat_id);
