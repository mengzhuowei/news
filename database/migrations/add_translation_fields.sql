-- 添加多语言翻译字段：简体中文、繁体中文、日语、俄语、德语、法语

ALTER TABLE articles ADD COLUMN title_zh_cn VARCHAR(1024) DEFAULT '' AFTER is_built;
ALTER TABLE articles ADD COLUMN title_zh_tw VARCHAR(1024) DEFAULT '' AFTER title_zh_cn;
ALTER TABLE articles ADD COLUMN title_ja    VARCHAR(1024) DEFAULT '' AFTER title_zh_tw;
ALTER TABLE articles ADD COLUMN title_ru    VARCHAR(1024) DEFAULT '' AFTER title_ja;
ALTER TABLE articles ADD COLUMN title_de    VARCHAR(1024) DEFAULT '' AFTER title_ru;
ALTER TABLE articles ADD COLUMN title_fr    VARCHAR(1024) DEFAULT '' AFTER title_de;

ALTER TABLE articles ADD COLUMN summary_zh_cn TEXT DEFAULT NULL AFTER title_fr;
ALTER TABLE articles ADD COLUMN summary_zh_tw TEXT DEFAULT NULL AFTER summary_zh_cn;
ALTER TABLE articles ADD COLUMN summary_ja    TEXT DEFAULT NULL AFTER summary_zh_tw;
ALTER TABLE articles ADD COLUMN summary_ru    TEXT DEFAULT NULL AFTER summary_ja;
ALTER TABLE articles ADD COLUMN summary_de    TEXT DEFAULT NULL AFTER summary_ru;
ALTER TABLE articles ADD COLUMN summary_fr    TEXT DEFAULT NULL AFTER summary_de;

ALTER TABLE articles ADD COLUMN content_zh_cn MEDIUMTEXT DEFAULT NULL AFTER summary_fr;
ALTER TABLE articles ADD COLUMN content_zh_tw MEDIUMTEXT DEFAULT NULL AFTER content_zh_cn;
ALTER TABLE articles ADD COLUMN content_ja    MEDIUMTEXT DEFAULT NULL AFTER content_zh_tw;
ALTER TABLE articles ADD COLUMN content_ru    MEDIUMTEXT DEFAULT NULL AFTER content_ja;
ALTER TABLE articles ADD COLUMN content_de    MEDIUMTEXT DEFAULT NULL AFTER content_ru;
ALTER TABLE articles ADD COLUMN content_fr    MEDIUMTEXT DEFAULT NULL AFTER content_de;
