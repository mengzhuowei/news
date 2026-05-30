-- 新闻聚合平台 — 数据库 Schema
-- MySQL 8.0+

CREATE DATABASE IF NOT EXISTS news_aggregator
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE news_aggregator;

-- 文章表
CREATE TABLE IF NOT EXISTS articles (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    url             VARCHAR(768)    NOT NULL UNIQUE,
    title           VARCHAR(1024)   NOT NULL,
    summary         TEXT            DEFAULT '',
    content         MEDIUMTEXT      DEFAULT '',
    source          VARCHAR(255)    NOT NULL,
    category        VARCHAR(100)    DEFAULT NULL,
    author          TEXT            DEFAULT 'Unknown',
    image_url       VARCHAR(1024)   DEFAULT '',
    slug            VARCHAR(256)    DEFAULT '',
    is_rewritten    BOOLEAN         DEFAULT FALSE,
    is_built        BOOLEAN         DEFAULT FALSE,
    published_at    DATETIME        DEFAULT NULL,
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_source    (source),
    INDEX idx_category  (category),
    INDEX idx_published (published_at),
    INDEX idx_rewritten (is_rewritten),
    INDEX idx_built     (is_built),
    INDEX idx_slug      (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 订阅表
CREATE TABLE IF NOT EXISTS subscriptions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    email           VARCHAR(255)    NOT NULL UNIQUE,
    is_active       BOOLEAN         DEFAULT TRUE,
    frequency       ENUM('daily', 'weekly') DEFAULT 'daily',
    site            VARCHAR(100)    DEFAULT NULL,
    categories      JSON            DEFAULT NULL,
    confirm_token   VARCHAR(64)     DEFAULT NULL,
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    confirmed_at    DATETIME        DEFAULT NULL,

    INDEX idx_email (email),
    INDEX idx_token (confirm_token)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 抓取日志表
CREATE TABLE IF NOT EXISTS fetch_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    source          VARCHAR(255)    NOT NULL,
    articles_count  INT             DEFAULT 0,
    status          ENUM('success', 'failed', 'partial') DEFAULT 'success',
    error_message   TEXT            DEFAULT NULL,
    started_at      DATETIME        DEFAULT NULL,
    completed_at    DATETIME        DEFAULT NULL,
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_source  (source),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 邮件日志表
CREATE TABLE IF NOT EXISTS email_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    subscription_id BIGINT          DEFAULT NULL,
    email_type      ENUM('confirmation', 'welcome', 'digest', 'unsubscribe') NOT NULL,
    status          ENUM('sent', 'failed', 'bounced') DEFAULT 'sent',
    sent_at         DATETIME        DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL,
    INDEX idx_subscription (subscription_id),
    INDEX idx_sent_at     (sent_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
