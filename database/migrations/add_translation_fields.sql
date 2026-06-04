-- 添加多语言翻译相关字段

ALTER TABLE articles ADD COLUMN is_translated BOOLEAN DEFAULT FALSE AFTER is_built;
ALTER TABLE articles ADD COLUMN translations JSON DEFAULT NULL AFTER is_translated;

ALTER TABLE articles ADD INDEX idx_translated (is_translated);
