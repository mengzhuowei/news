-- 添加 Telegram 推送相关字段

ALTER TABLE subscriptions ADD COLUMN chat_id BIGINT DEFAULT NULL AFTER email;
ALTER TABLE subscriptions ADD COLUMN push_time VARCHAR(8) DEFAULT '09:00:00' AFTER frequency;
ALTER TABLE subscriptions ADD COLUMN timezone VARCHAR(50) DEFAULT 'UTC' AFTER push_time;
ALTER TABLE subscriptions ADD COLUMN push_channel ENUM('telegram', 'email') DEFAULT 'telegram' AFTER timezone;
ALTER TABLE subscriptions ADD COLUMN last_pushed_at DATETIME DEFAULT NULL AFTER confirmed_at;

ALTER TABLE subscriptions ADD INDEX idx_chat_id (chat_id);
ALTER TABLE subscriptions ADD INDEX idx_push_time (push_time);

ALTER TABLE subscriptions MODIFY COLUMN email VARCHAR(255) DEFAULT NULL;

ALTER TABLE subscriptions DROP INDEX email;
ALTER TABLE subscriptions ADD UNIQUE INDEX idx_chat_id_unique (chat_id);
