-- 将 subscriptions.email 从普通索引升级为唯一索引

ALTER TABLE subscriptions DROP INDEX idx_email;
ALTER TABLE subscriptions ADD UNIQUE INDEX idx_email_unique (email);
