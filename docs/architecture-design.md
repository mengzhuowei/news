# 新闻聚合平台 — 架构设计文档

> 版本：2.0 | 最后更新：2026-05-29

---

## 1. 系统架构

### 1.1 整体架构图

```
                         ┌─────────────────────────────┐
                         │         用户 / 爬虫          │
                         └──────┬──────────────┬────────┘
                                │              │
                          HTTP  │              │  HTTP
                                ▼              ▼
                    ┌──────────────────────────────────┐
                    │         FastAPI :8000            │
                    │    (静态站点 + API 服务)          │
                    └──────────────┬───┬───┬───────────┘
                                   │   │   │
                      ┌────────────┘   │   └───────────┐
                      ▼                ▼               ▼
              ┌──────────┐       ┌────────┐      ┌──────────┐
              │ MySQL    │       │SendGrid│      │  LLM API │
              │ :3306    │       │ (邮件)  │      │OpenAI兼容│
              └──────┬───┘       └────────┘      └──────────┘
                     ▲
                     │
            ┌────────┴────────┐
            │  Fetcher 服务     │
            │  (定时抓取+处理)   │
            │                  │
            │ ┌──────────────┐ │
            │ │ Aggregator   │ │
            │ │ ContentEnrich│ │
            │ │ Cleaner      │ │
            │ │ Summarizer   │ │
            │ │ SiteBuilder  │ │
            │ └──────────────┘ │
            └─────────────────┘
```

### 1.2 服务清单

| 服务 | 技术栈 | 端口 | 说明 |
|------|--------|------|------|
| `web` | FastAPI + Uvicorn | 8000 | API 服务 + 静态站点 |
| `fetcher` | Python asyncio | — | 定时抓取调度器 |
| `mysql` | MySQL 8.0 | 3306 | 数据持久化 |

### 1.3 Docker Compose 拓扑

```yaml
services:
  mysql:     # 数据层
  fetcher:   # 抓取层 (depends_on: mysql)
  web:       # 服务层 (depends_on: mysql)
```

所有服务通过 `news-network` 桥接网络通信。

---

## 2. 项目目录结构

```
News/
├── fetcher/                          # 数据抓取层
│   ├── __init__.py                   # 延迟导入，避免重型依赖
│   ├── aggregator.py                 # 聚合调度器
│   ├── cleaner.py                    # SimHash 去重 + 内容清洗
│   ├── summarizer.py                 # AI 摘要 + 重写
│   ├── content_enricher.py           # RSS 缺全文时回源抓取
│   ├── site_builder.py               # Jinja2 静态站点构建
│   ├── scheduler.py                  # 定时任务调度 + DB 读写
│   ├── requirements.txt
│   └── fetchers/                     # 各新闻源抓取器
│       ├── __init__.py
│       ├── base.py                   # 抽象基类 + Article 数据类
│       ├── bbc.py
│       ├── reuters.py
│       ├── bloomberg.py
│       ├── ft.py
│       ├── techcrunch.py
│       ├── economist.py
│       └── arxiv.py
│
├── web/                              # Web 服务层
│   ├── __init__.py
│   ├── app.py                        # FastAPI 主应用
│   ├── database.py                   # SQLAlchemy Engine + Session
│   ├── models.py                     # ORM 模型
│   ├── email_service.py              # SendGrid 邮件服务
│   ├── requirements.txt
│   └── routes/
│       ├── __init__.py               # 延迟导入
│       ├── admin.py                  # 管理 API
│       ├── subscribe.py              # 订阅 API
│       └── subscription.py           # 订阅管理 API
│
├── shared/                           # 共享资源
│   ├── scripts/
│   │   ├── build.sh                  # 构建脚本
│   │   └── deploy.sh                 # 部署脚本
│   └── templates/
│       └── jinja2/                   # Jinja2 模板
│           ├── base.html
│           ├── index.html
│           ├── list.html
│           └── single.html
│
├── database/                         # 数据库
│   ├── schema.sql                    # 完整 Schema
│   └── migrations/                   # 增量迁移脚本
│
├── admin/                            # 管理后台前端
│   └── index.html
│
├── tests/                            # 测试
├── docker-compose.yml
├── Dockerfile.fetcher
├── Dockerfile.web
└── .github/workflows/deploy.yml      # CI/CD
```

---

## 3. 数据表设计

### 3.1 ER 关系图

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────┐
│   articles   │       │  subscriptions   │       │  email_logs  │
├──────────────┤       ├──────────────────┤       ├──────────────┤
│ id (PK)      │       │ id (PK)          │◄──────│ id (PK)      │
│ url (UQ)     │       │ email (UQ)       │       │ subscription │
│ title        │       │ is_active        │       │  _id (FK)    │
│ summary      │       │ frequency        │       │ email_type   │
│ content      │       │ site             │       │ status       │
│ source       │       │ categories       │       │ sent_at      │
│ category     │       │ confirm_token    │       └──────────────┘
│ author       │       │ created_at       │
│ image_url    │       │ confirmed_at     │
│ slug         │       └──────────────────┘
│ is_rewritten │
│ is_built     │       ┌──────────────────┐
│ published_at │       │   fetch_logs     │
│ created_at   │       ├──────────────────┤
└──────────────┘       │ id (PK)          │
                       │ source           │
                       │ articles_count   │
                       │ status           │
                       │ error_message    │
                       │ started_at       │
                       │ completed_at     │
                       │ created_at       │
                       └──────────────────┘
```

### 3.2 articles 表

```sql
CREATE TABLE articles (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    url             VARCHAR(768)    NOT NULL UNIQUE,     -- 文章原始 URL（去重依据）
    title           VARCHAR(1024)   NOT NULL,            -- 文章标题
    summary         TEXT            DEFAULT '',           -- AI 生成摘要
    content         MEDIUMTEXT      DEFAULT '',           -- 正文（清洗/重写后）
    source          VARCHAR(255)    NOT NULL,             -- 来源名称（BBC News / Reuters 等）
    category        VARCHAR(100)    DEFAULT NULL,         -- 分类（world/technology/finance 等）
    author          TEXT            DEFAULT 'Unknown',    -- 作者（TEXT 因 arXiv 作者列表极长）
    image_url       VARCHAR(1024)   DEFAULT '',           -- 封面图 URL
    slug            VARCHAR(256)    DEFAULT '',           -- URL 友好标识，用于文件名
    is_rewritten    BOOLEAN         DEFAULT FALSE,        -- 是否经过 AI 重写
    is_built        BOOLEAN         DEFAULT FALSE,        -- 是否已生成静态页面
    published_at    DATETIME        DEFAULT NULL,         -- 原始发布时间
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,  -- 入库时间

    INDEX idx_source    (source),
    INDEX idx_category  (category),
    INDEX idx_published (published_at),
    INDEX idx_rewritten (is_rewritten),
    INDEX idx_built     (is_built),
    INDEX idx_slug      (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**设计说明**：
- `url` 设为 UNIQUE，作为业务层去重依据
- `content` 使用 MEDIUMTEXT（最大 16MB），支持长文存储
- `author` 从 VARCHAR(255) 扩展为 TEXT，因为 arXiv 论文作者列表可达数千字符
- `slug` 由 `scheduler.make_slug()` 生成，格式为 `{source}_{title前50字符}`
- `is_rewritten` / `is_built` 为布尔标记，用于管理端筛选和构建状态追踪

### 3.3 subscriptions 表

```sql
CREATE TABLE subscriptions (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    email           VARCHAR(255)    NOT NULL UNIQUE,     -- 订阅邮箱
    is_active       BOOLEAN         DEFAULT TRUE,        -- 是否活跃
    frequency       ENUM('daily', 'weekly')
                                    DEFAULT 'daily',     -- 推送频率
    site            VARCHAR(100)    DEFAULT NULL,        -- 订阅站点
    categories      JSON            DEFAULT NULL,        -- JSON 数组：["news","tech"]
    confirm_token   VARCHAR(64)     DEFAULT NULL,        -- 确认令牌（确认后保留，用于后续身份验证）
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,
    confirmed_at    DATETIME        DEFAULT NULL,        -- 确认时间

    INDEX idx_email (email),
    INDEX idx_token (confirm_token)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**设计说明**：
- `confirm_token` 使用 `secrets.token_urlsafe(32)` 生成，确认后保留，用于后续身份验证
- `categories` 存储为 MySQL 原生 JSON 类型，支持 JSON 查询和验证
- `frequency` 使用 ENUM 约束合法值

### 3.4 fetch_logs 表

```sql
CREATE TABLE fetch_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    source          VARCHAR(255)    NOT NULL,            -- 抓取源名称
    articles_count  INT             DEFAULT 0,           -- 抓取文章数
    status          ENUM('success', 'failed', 'partial')
                                    DEFAULT 'success',   -- 抓取状态
    error_message   TEXT            DEFAULT NULL,        -- 错误信息
    started_at      DATETIME        DEFAULT NULL,        -- 开始时间
    completed_at    DATETIME        DEFAULT NULL,        -- 完成时间
    created_at      DATETIME        DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_source  (source),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.5 email_logs 表

```sql
CREATE TABLE email_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    subscription_id BIGINT          DEFAULT NULL,        -- 关联订阅
    email_type      ENUM('confirmation', 'welcome', 'digest', 'unsubscribe')
                                    NOT NULL,            -- 邮件类型
    status          ENUM('sent', 'failed', 'bounced')
                                    DEFAULT 'sent',      -- 发送状态
    sent_at         DATETIME        DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL,
    INDEX idx_subscription (subscription_id),
    INDEX idx_sent_at     (sent_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.6 迁移脚本

| 迁移文件 | 变更内容 |
|----------|----------|
| `add_summary_content_author.sql` | 新增 summary, content, author, image_url 字段 |
| `alter_author_to_text.sql` | author 从 VARCHAR(255) 扩展为 TEXT |
| `add_slug.sql` | 新增 slug 字段 + 索引 |
| `add_is_built.sql` | 新增 is_built 字段 + 索引 |

---

## 4. API 设计

### 4.1 订阅 API

> **安全说明**：查询、修改和取消订阅需通过确认 token 验证身份，或在用户界面要求用户输入邮箱并发送验证链接后操作。

#### POST /api/subscribe/

创建订阅。

**请求体**：
```json
{
  "email": "user@example.com",
  "frequency": "daily",
  "site": "news.com",
  "categories": ["technology", "finance"]
}
```

**成功响应** `200`：
```json
{
  "id": 1,
  "email": "user@example.com",
  "is_active": true,
  "frequency": "daily",
  "site": "news.com",
  "categories": ["technology", "finance"],
  "confirmed_at": null
}
```

**已订阅** `400`：
```json
{
  "detail": "Already subscribed"
}
```

#### GET /api/subscribe/confirm/{token}

确认订阅。

**成功响应** `200`：
```json
{
  "status": "confirmed",
  "email": "user@example.com"
}
```

**无效 Token** `404`：
```json
{
  "detail": "Invalid token"
}
```

**已确认** `200`：
```json
{
  "status": "already_confirmed",
  "email": "user@example.com"
}
```

#### DELETE /api/subscribe/{email}

取消订阅。

**成功响应** `200`：
```json
{
  "status": "unsubscribed"
}
```

#### GET /api/subscriptions/{email}

查询订阅详情。

**成功响应** `200`：
```json
{
  "email": "user@example.com",
  "is_active": true,
  "frequency": "daily",
  "site": "news.com",
  "categories": ["technology", "finance"],
  "confirmed_at": "2026-05-29T10:00:00"
}
```

#### PATCH /api/subscriptions/{email}

更新订阅偏好。

**请求体**：
```json
{
  "frequency": "weekly",
  "categories": ["technology"]
}
```

**成功响应** `200`：
```json
{
  "status": "updated"
}
```

### 4.2 管理 API

> **所有 `/api/admin/*` 端点均需 HTTP Basic Auth 认证**（`ADMIN_USERNAME` / `ADMIN_PASSWORD`）。

#### GET /api/admin/stats

站点统计。

**响应**：
```json
{
  "total_articles": 1234,
  "total_subscriptions": 56,
  "active_subscriptions": 42,
  "rewritten_articles": 800,
  "unrewritten_articles": 434,
  "built_articles": 1200,
  "unbuilt_articles": 34
}
```

#### GET /api/admin/articles

文章列表（支持筛选和分页）。

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `source` | string | — | 按来源筛选 |
| `is_built` | bool | — | 按构建状态筛选 |
| `is_rewritten` | bool | — | 按重写状态筛选 |
| `skip` | int | 0 | 偏移量 |
| `limit` | int | 20 | 每页数量（1-100） |

**响应**：
```json
{
  "total": 1234,
  "items": [
    {
      "id": 1,
      "url": "https://www.bbc.com/news/...",
      "title": "Article Title",
      "source": "BBC News",
      "category": "technology",
      "slug": "bbc_news_article-title",
      "is_rewritten": true,
      "is_built": true,
      "published_at": "2026-05-29T10:00:00",
      "created_at": "2026-05-29T10:05:00"
    }
  ]
}
```

#### GET /api/admin/articles/sources

按来源统计文章数。

> **注意**：此端点必须在 `/{article_id}` 之前注册，否则 FastAPI 会将 `sources` 误匹配为路径参数。

**响应**：
```json
[
  {"source": "BBC News", "count": 320},
  {"source": "Reuters", "count": 280},
  {"source": "Bloomberg", "count": 210}
]
```

#### GET /api/admin/articles/{article_id}

文章详情。

**响应**：
```json
{
  "id": 1,
  "url": "https://www.bbc.com/news/...",
  "title": "Article Title",
  "source": "BBC News",
  "category": "technology",
  "slug": "bbc_news_article-title",
  "is_rewritten": true,
  "is_built": true,
  "has_md_file": true,
  "published_at": "2026-05-29T10:00:00",
  "created_at": "2026-05-29T10:05:00"
}
```

#### DELETE /api/admin/articles/{article_id}

删除文章（同时删除关联的 Markdown 文件）。

**响应**：
```json
{
  "status": "deleted",
  "md_file_deleted": true
}
```

#### POST /api/admin/articles/cleanup

清理过期文章。

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `days` | int | 7 | 保留天数（1-90） |

**响应**：
```json
{
  "removed_files": 15,
  "removed_db": 23
}
```

#### GET /api/admin/fetch/logs

抓取日志。

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `source` | string | — | 按来源筛选 |
| `skip` | int | 0 | 偏移量 |
| `limit` | int | 20 | 每页数量 |

**响应**：
```json
{
  "total": 150,
  "items": [
    {
      "id": 1,
      "source": "BBC News",
      "articles_count": 25,
      "status": "success",
      "error_message": null,
      "started_at": "2026-05-29T10:00:00",
      "completed_at": "2026-05-29T10:00:15",
      "created_at": "2026-05-29T10:00:00"
    }
  ]
}
```

#### GET /api/admin/subscriptions

订阅用户列表（分页）。

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `skip` | int | 0 | 偏移量 |
| `limit` | int | 20 | 每页数量（1-100） |

**响应**：
```json
{
  "total": 56,
  "items": [
    {
      "id": 1,
      "email": "user@example.com",
      "is_active": true,
      "frequency": "daily",
      "site": "news.com",
      "categories": ["technology"],
      "confirmed_at": "2026-05-29T10:00:00",
      "created_at": "2026-05-29T09:55:00"
    }
  ]
}
```

#### GET /api/admin/email/logs

邮件发送日志（分页）。

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `email_type` | string | — | 按邮件类型筛选（confirmation/welcome/digest/unsubscribe） |
| `status` | string | — | 按发送状态筛选（sent/failed/bounced） |
| `skip` | int | 0 | 偏移量 |
| `limit` | int | 20 | 每页数量（1-100） |

**响应**：
```json
{
  "total": 120,
  "items": [
    {
      "id": 1,
      "subscription_id": 5,
      "email_type": "confirmation",
      "status": "sent",
      "sent_at": "2026-05-29T10:00:00"
    }
  ]
}
```

#### POST /api/admin/build

触发站点构建（后台线程执行）。

**成功响应** `200`：
```json
{
  "status": "started",
  "message": "站点构建已启动"
}
```

**构建中** `409`：
```json
{
  "detail": "构建正在进行中，请稍后再试"
}
```

#### GET /api/admin/build/status

查询构建状态。

**响应**：
```json
{
  "status": "success",
  "started_at": "2026-05-29T10:00:00",
  "finished_at": "2026-05-29T10:00:25",
  "message": "站点构建成功",
  "articles_count": 1200
}
```

---

## 5. 功能模块详细设计与示例代码

### 5.1 数据抓取模块

#### 5.1.1 Article 数据类

```python
# fetcher/fetchers/base.py

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Article:
    """文章数据结构 — 贯穿抓取、清洗、存储全流程"""
    title: str                           # 文章标题
    url: str                             # 原始 URL（去重依据）
    content: str                         # 正文（HTML 或纯文本）
    summary: str                         # 摘要
    published: datetime                  # 发布时间
    source: str                          # 来源名称
    category: str                        # 分类
    author: Optional[str] = None         # 作者
    image_url: Optional[str] = None      # 封面图
    language: str = "en"                 # 语言
    rewritten: bool = False              # 是否经过 AI 重写
```

#### 5.1.2 BaseFetcher 抽象基类

```python
# fetcher/fetchers/base.py

from abc import ABC, abstractmethod
from typing import Optional, List
from datetime import datetime, timezone
import feedparser
import httpx
from bs4 import BeautifulSoup
from loguru import logger


class BaseFetcher(ABC):
    """抓取器抽象基类 — 所有新闻源抓取器必须继承此类"""

    def __init__(self, source_name: str, source_url: str, category: str):
        self.source_name = source_name
        self.source_url = source_url
        self.category = category
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_rss(self, rss_url: str) -> List[Article]:
        """通用 RSS 抓取方法"""
        try:
            response = await self.client.get(rss_url)
            response.raise_for_status()
            feed = feedparser.parse(response.text)

            articles = []
            for entry in feed.entries:
                article = self._parse_rss_entry(entry)
                if article:
                    articles.append(article)

            logger.info(f"[{self.source_name}] Fetched {len(articles)} from RSS")
            return articles
        except Exception as e:
            logger.error(f"[{self.source_name}] RSS fetch failed: {e}")
            return []

    def _parse_rss_entry(self, entry) -> Optional[Article]:
        """解析单个 RSS 条目为 Article 对象"""
        from email.utils import parsedate_to_datetime
        try:
            title = entry.get('title', '')
            url = entry.get('link', '')
            summary = entry.get('summary', entry.get('description', ''))
            published_str = entry.get('published', entry.get('updated', ''))

            try:
                published = parsedate_to_datetime(published_str)
            except Exception:
                published = datetime.now(timezone.utc)

            content = summary
            if 'content' in entry:
                content = entry.content[0].value

            return Article(
                title=title,
                url=url,
                content=content,
                summary=self._clean_text(summary),
                published=published,
                source=self.source_name,
                category=self.category,
                author=entry.get('author', ''),
                image_url=self._extract_image(entry),
            )
        except Exception as e:
            logger.error(f"[{self.source_name}] Parse error: {e}")
            return None

    def _clean_text(self, text: str) -> str:
        """清洗 HTML 标签，返回纯文本"""
        import re
        text = re.sub(r'<[^>]+>', '', text)
        return text.strip()

    def _extract_image(self, entry) -> Optional[str]:
        """从 RSS 条目提取图片，按优先级：media:content → media:thumbnail → enclosures → HTML <img>"""
        if 'media_content' in entry:
            for media in entry.media_content:
                if media.get('medium') == 'image' or media.get('url', '').lower().endswith(
                    ('.jpg', '.jpeg', '.png', '.webp')
                ):
                    return media['url']

        if 'media_thumbnail' in entry and entry.media_thumbnail:
            return entry.media_thumbnail[0].get('url')

        if 'enclosures' in entry and entry.enclosures:
            for enc in entry.enclosures:
                if enc.get('type', '').startswith('image/'):
                    return enc.get('href') or enc.get('url')

        for field in ('content', 'summary', 'description'):
            html = entry.get(field, '')
            if isinstance(html, list) and html:
                html = html[0].get('value', '')
            if html:
                soup = BeautifulSoup(html, 'lxml')
                img = soup.find('img')
                if img and img.get('src', '').startswith(('http://', 'https://')):
                    return img['src']

        return None

    @abstractmethod
    async def fetch(self) -> List[Article]:
        """子类必须实现：执行抓取并返回文章列表"""
        pass

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
```

#### 5.1.3 具体抓取器示例（BBC）

```python
# fetcher/fetchers/bbc.py

from .base import BaseFetcher, Article
from loguru import logger


class BBCFetcher(BaseFetcher):
    """BBC News 抓取器 — 从 4 个分类 RSS 源并发抓取"""

    RSS_URLS = {
        'world': 'https://feeds.bbci.co.uk/news/world/rss.xml',
        'technology': 'https://feeds.bbci.co.uk/news/technology/rss.xml',
        'business': 'https://feeds.bbci.co.uk/news/business/rss.xml',
        'science': 'https://feeds.bbci.co.uk/news/science_and_environment/rss.xml',
    }

    def __init__(self):
        super().__init__(
            source_name="BBC News",
            source_url="https://www.bbc.com/news",
            category="news"
        )

    async def fetch(self) -> list[Article]:
        all_articles = []
        for category, rss_url in self.RSS_URLS.items():
            articles = await self.fetch_rss(rss_url)
            for article in articles:
                article.category = category
            all_articles.extend(articles)

        logger.info(f"[BBC] Total fetched: {len(all_articles)} articles")
        return all_articles
```

#### 5.1.4 聚合调度器

```python
# fetcher/aggregator.py

from fetcher.fetchers.bbc import BBCFetcher
from fetcher.fetchers.reuters import ReutersFetcher
from fetcher.fetchers.bloomberg import BloombergFetcher
from fetcher.fetchers.ft import FTWatcher
from fetcher.fetchers.techcrunch import TechCrunchFetcher
from fetcher.fetchers.economist import EconomistFetcher
from fetcher.fetchers.arxiv import ArxivFetcher


class Aggregator:
    """新闻聚合调度器 — 管理所有抓取器实例"""

    def __init__(self):
        self.fetchers = [
            BBCFetcher(),
            ReutersFetcher(),
            BloombergFetcher(),
            FTWatcher(),
            TechCrunchFetcher(),
            EconomistFetcher(),
            ArxivFetcher(),
        ]

    async def close_all(self):
        for fetcher in self.fetchers:
            await fetcher.close()
```

### 5.2 内容充实模块

```python
# fetcher/content_enricher.py

import asyncio
import re
import time
from typing import Optional, List
from urllib.parse import urlparse
from loguru import logger
from fetcher.fetchers.base import Article

_DOMAIN_DELAY: dict[str, float] = {}
_DEFAULT_DELAY = 2.0


class ContentEnricher:
    """内容充实器 — 为 RSS 缺少全文的文章抓取网页正文"""

    def __init__(self, enabled: bool = True, max_concurrent: int = 3, request_timeout: float = 30.0):
        self.enabled = enabled
        self.max_concurrent = max_concurrent
        self.request_timeout = request_timeout
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._session: Optional[object] = None

        if not self.enabled:
            return

        try:
            from curl_cffi.requests import AsyncSession as CurlAsyncSession
            self._curl_cls = CurlAsyncSession
        except ImportError:
            self.enabled = False
            return

        try:
            import trafilatura
            self._trafilatura = trafilatura
        except ImportError:
            self.enabled = False
            return

    async def _get_session(self):
        """获取或创建 curl_cffi 会话（懒加载）"""
        if self._session is None:
            self._session = self._curl_cls(timeout=self.request_timeout)
        return self._session

    async def _return_session(self, session):
        """归还会话（保持连接复用）"""
        pass  # 保持会话打开以复用

    async def close(self):
        """关闭会话，释放资源"""
        if self._session:
            await self._session.close()

    def _needs_enrichment(self, article: Article) -> bool:
        """判断文章是否需要充实：内容为空或过短且与摘要相同"""
        if not article.content:
            return True
        content_text = re.sub(r'<[^>]+>', '', article.content).strip()
        if not content_text:
            return True
        if len(content_text) < 300:
            summary_text = re.sub(r'<[^>]+>', '', article.summary).strip()
            if content_text == summary_text:
                return True
        return False

    async def _rate_limit(self, url: str):
        """按域名限速，同一域名至少间隔 _DEFAULT_DELAY 秒"""
        domain = urlparse(url).netloc
        last_time = _DOMAIN_DELAY.get(domain, 0)
        wait = max(0, _DEFAULT_DELAY - (time.monotonic() - last_time))
        if wait > 0:
            await asyncio.sleep(wait)
        _DOMAIN_DELAY[domain] = time.monotonic()

    async def _fetch_html(self, url: str) -> Optional[str]:
        """使用 curl_cffi 抓取网页 HTML"""
        session = await self._get_session()
        try:
            await self._rate_limit(url)
            resp = await session.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": f"https://{urlparse(url).netloc}/",
                },
                allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.text
            return None
        except Exception:
            return None
        finally:
            await self._return_session(session)

    def _extract_content(self, html: str, url: str) -> Optional[str]:
        """使用 trafilatura 提取正文"""
        text = self._trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        return text if text and len(text) > 100 else None

    async def enrich(self, article: Article) -> Article:
        """充实单篇文章"""
        if not self.enabled or not self._needs_enrichment(article) or not article.url:
            return article

        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)

        async with self._semaphore:
            html = await self._fetch_html(article.url)
            if html:
                full_text = await asyncio.to_thread(self._extract_content, html, article.url)
                if full_text:
                    article.content = full_text
            return article

    async def enrich_batch(self, articles: List[Article]) -> List[Article]:
        """批量充实文章"""
        if not self.enabled:
            return articles

        tasks = [self.enrich(a) for a in articles]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        enriched = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                enriched.append(articles[i])
            else:
                enriched.append(result)
        return enriched
```

### 5.3 内容清洗模块

```python
# fetcher/cleaner.py

import re
import hashlib
import numpy as np
from typing import Optional, Set
from loguru import logger
from fetcher.fetchers.base import Article


class SimHash:
    """SimHash 算法 — 用于检测相似内容"""

    def __init__(self, hash_bits: int = 64):
        self.hash_bits = hash_bits

    def _get_features(self, text: str) -> list[str]:
        """3-gram 分词提取特征"""
        text = re.sub(r'[^\w\s]', '', text.lower())
        words = text.split()
        return [' '.join(words[i:i+3]) for i in range(len(words) - 2)]

    def compute(self, text: str) -> int:
        """计算文本的 SimHash 值"""
        features = self._get_features(text)
        if not features:
            return 0

        v = np.zeros(self.hash_bits, dtype=int)
        for feature in features:
            hash_val = int(hashlib.md5(feature.encode()).hexdigest(), 16) % (2 ** self.hash_bits)
            for i in range(self.hash_bits):
                v[i] += 1 if (hash_val >> i) & 1 else -1

        result = 0
        for i in range(self.hash_bits):
            if v[i] > 0:
                result |= (1 << i)
        return result

    def hamming_distance(self, hash1: int, hash2: int) -> int:
        """汉明距离"""
        x = hash1 ^ hash2
        count = 0
        while x:
            count += 1
            x &= x - 1
        return count

    def is_similar(self, text1: str, text2: str, threshold: float = 0.85) -> bool:
        """判断两段文本是否相似"""
        distance = self.hamming_distance(self.compute(text1), self.compute(text2))
        similarity = 1 - (distance / self.hash_bits)
        return similarity >= threshold


class ContentCleaner:
    """AI 内容清洗器 — 去重 + HTML 清洗 + 广告移除 + 截断 + 版权声明"""

    def __init__(self, duplicate_threshold: float = 0.8):
        self.duplicate_threshold = duplicate_threshold
        self.simhash = SimHash()
        self._seen_hashes: Set[int] = set()
        self._seen_titles: Set[str] = set()

    def clean(self, article: Article) -> Optional[Article]:
        """清洗文章，重复则返回 None"""
        try:
            if self._is_duplicate(article):
                return None

            article.content = self._remove_html_tags(article.content)
            article.summary = self._remove_html_tags(article.summary)
            article.content = self._remove_ads(article.content)
            article.summary = self._remove_ads(article.summary)
            article.content = self._normalize_text(article.content)
            article.summary = self._normalize_text(article.summary)

            article_hash = self.simhash.compute(article.title + article.content)
            self._seen_hashes.add(article_hash)
            self._seen_titles.add(article.title.lower())

            article.content = self._truncate_content(article.content, max_length=5000)

            article.content = self._add_copyright_notice(article, article.content)
            return article
        except Exception as e:
            logger.error(f"[Cleaner] Error: {e}")
            return None

    def _is_duplicate(self, article: Article) -> bool:
        """三级去重：标题匹配 → SimHash 模糊匹配（基于预处理后内容）"""
        if article.title.lower() in self._seen_titles:
            return True

        preprocessed = self._remove_html_tags(article.content)
        preprocessed = self._remove_ads(preprocessed)
        preprocessed = self._normalize_text(preprocessed)
        article_hash = self.simhash.compute(article.title + preprocessed)

        if article_hash in self._seen_hashes:
            return True
        for seen_hash in self._seen_hashes:
            distance = self.simhash.hamming_distance(article_hash, seen_hash)
            if 1 - (distance / self.simhash.hash_bits) >= self.duplicate_threshold:
                return True
        return False

    def _remove_html_tags(self, text: str) -> str:
        import html as html_module
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        return html_module.unescape(text)

    def _remove_ads(self, text: str) -> str:
        patterns = [
            r'Subscribe to.*?(?:now|today)',
            r'Sign up for.*?(?:newsletter|digest)',
            r'Cookie.*?Policy',
            r'Privacy.*?Policy',
        ]
        for pattern in patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        return text

    def _normalize_text(self, text: str) -> str:
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s.,!?;:()\'\"\-–—\u4e00-\u9fff\u00C0-\u024F\u0400-\u04FF\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7AF]', '', text)
        return text.strip()

    def _truncate_content(self, text: str, max_length: int = 5000) -> str:
        if len(text) <= max_length:
            return text
        truncated = text[:max_length]
        last_period = truncated.rfind('.')
        if last_period > max_length * 0.8:
            return truncated[:last_period + 1]
        return truncated + "..."

    def _add_copyright_notice(self, article: Article, content: str) -> str:
        notice = f"\n\n---\n**Source:** [{article.source}]({article.url})\n"
        if article.published:
            notice += f"*Generated at: {article.published.strftime('%Y-%m-%d %H:%M:%S')} UTC*\n"
        return content + notice
```

### 5.4 AI 摘要与重写模块

```python
# fetcher/summarizer.py

import os
from typing import Optional, Tuple
from loguru import logger


class Summarizer:
    """文章摘要生成器 — 支持所有兼容 OpenAI 协议的模型"""

    REWRITE_SYSTEM_PROMPT = (
        "You are a professional news editor. Rewrite the following article to make it unique "
        "while preserving all key facts, data, and quotes. Keep the same language as the original. "
        "Change sentence structures, use synonyms, and rephrase paragraphs. "
        "Do NOT add information that isn't in the original. "
        "Output ONLY the rewritten article, no explanations."
    )

    REWRITE_SAFE_PROMPT = (
        "You are a professional news editor. Rewrite the following article in a neutral, factual tone. "
        "Keep the same language as the original. Focus strictly on objective facts, numbers, and data. "
        "Completely avoid any opinions, commentary, political references, or controversial statements. "
        "Output ONLY the rewritten article, no explanations."
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        # 优先级：参数 > LLM_* > OPENAI_* > 默认值
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-3.5-turbo"
        self.max_summary_length = 200
        self._client: Optional[object] = None

    @property
    def client(self):
        """懒加载 OpenAI 客户端（复用连接）"""
        if self._client is None and self.api_key:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    async def summarize(self, title: str, content: str) -> str:
        """基础摘要（无 LLM）— 取首句截断"""
        if len(content) <= self.max_summary_length:
            return content
        summary = content.split('.')[0]
        if len(summary) > self.max_summary_length:
            summary = summary[:self.max_summary_length].rsplit(' ', 1)[0] + '...'
        return summary.strip()

    async def summarize_with_ai(self, title: str, content: str) -> str:
        """AI 摘要 — 调用 LLM 生成"""
        if not self.api_key:
            return await self.summarize(title, content)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Create a concise summary in no more than 200 characters. "
                                   "Focus on main points and key information."
                    },
                    {
                        "role": "user",
                        "content": f"Title: {title}\n\nContent: {content[:4000]}\n\nSummary:"
                    }
                ],
                max_tokens=150,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[Summarizer] AI failed: {e}, falling back")
            return await self.summarize(title, content)

    async def rewrite(self, title: str, content: str) -> Tuple[str, str]:
        """AI 重写（洗稿）— 返回 (rewritten_content, rewritten_summary)"""
        if not self.api_key:
            return content, content[:200]

        truncated = content[:4000]

        # 第一次尝试：标准 Prompt
        rewritten = await self._call_rewrite(self.client, title, truncated, self.REWRITE_SYSTEM_PROMPT)

        # 失败重试：安全 Prompt
        if rewritten is None:
            rewritten = await self._call_rewrite(self.client, title, truncated, self.REWRITE_SAFE_PROMPT)

        # 仍失败则返回原文
        if rewritten is None:
            return content, content[:200]

        # 生成摘要
        summary = await self._call_summary(self.client, title, rewritten)
        if summary is None:
            summary = content[:200]

        return rewritten, summary

    async def _call_rewrite(self, client, title: str, content: str, system_prompt: str) -> Optional[str]:
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Title: {title}\n\nOriginal:\n{content}\n\nRewrite:"}
                ],
                max_tokens=1500,
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"[Summarizer] Rewrite failed: {e}")
            return None

    async def _call_summary(self, client, title: str, content: str) -> Optional[str]:
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "1-2 sentence summary. Max 200 characters."},
                    {"role": "user", "content": f"Title: {title}\n\n{content[:2000]}\n\nSummary:"}
                ],
                max_tokens=100,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"[Summarizer] Summary failed: {e}")
            return None
```

### 5.5 定时调度模块

```python
# fetcher/scheduler.py

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from loguru import logger

from fetcher.aggregator import Aggregator
from fetcher.cleaner import ContentCleaner
from fetcher.summarizer import Summarizer
from fetcher.content_enricher import ContentEnricher


def make_slug(source: str, title: str) -> str:
    """生成 URL 友好的 slug"""
    safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_'))
    safe_title = safe_title.strip().replace(' ', '-')
    return f"{source.lower()}_{safe_title[:50]}"


class FetchScheduler:
    """定时抓取调度器 — 协调全流程"""

    def __init__(self, interval_minutes: int = 15):
        self.interval_minutes = interval_minutes
        self.aggregator = Aggregator()
        self.cleaner = ContentCleaner()
        self.summarizer = Summarizer()
        self.enricher = ContentEnricher(
            enabled=os.getenv("CONTENT_ENRICHMENT", "true").lower() == "true",
            max_concurrent=int(os.getenv("ENRICHMENT_CONCURRENT", "3")),
        )

    async def fetch_and_process(self):
        """完整抓取流程：抓取 → 充实 → 去重 → 清洗 → 洗稿 → 入库 → 清理 → 构建"""
        logger.info(f"[{datetime.now(timezone.utc)}] Starting scheduled fetch...")

        try:
            # 1. 并发抓取（逐源记录 FetchLog）
            tasks = [self._fetch_source(f) for f in self.aggregator.fetchers]
            results = await asyncio.gather(*tasks)
            all_articles = [a for batch in results for a in batch]

            # URL 精确去重
            seen_urls = set()
            articles = []
            for article in all_articles:
                if article.url not in seen_urls:
                    seen_urls.add(article.url)
                    articles.append(article)

            if not articles:
                return

            # 2. 充实
            if self.enricher.enabled:
                articles = await self.enricher.enrich_batch(articles)

            # 3. 逐篇处理
            saved_count = 0
            for article in articles:
                if self._is_in_db(article.url):
                    continue

                cleaned = self.cleaner.clean(article)
                if not cleaned:
                    continue

                # AI 洗稿
                if self.summarizer.api_key:
                    rewritten_content, rewritten_summary = await self.summarizer.rewrite(
                        cleaned.title, cleaned.content
                    )
                    cleaned.content = rewritten_content
                    cleaned.summary = rewritten_summary
                    cleaned.rewritten = True
                else:
                    cleaned.rewritten = False

                self._save_to_db(cleaned)
                saved_count += 1

            # 4. 清理过期
            self._cleanup_expired_articles(days=7)

            # 5. 构建站点
            if saved_count > 0:
                self._build_static_site()

            logger.info(f"Fetch complete: {saved_count} saved")

        except Exception as e:
            logger.error(f"Fetch error: {e}")

    async def shutdown(self):
        """清理所有资源（进程退出时调用）"""
        await self.aggregator.close_all()
        await self.enricher.close()

    async def _fetch_source(self, fetcher) -> list:
        """抓取单个源并记录 FetchLog"""
        started_at = datetime.now(timezone.utc)
        try:
            articles = await fetcher.fetch()
            self._save_fetch_log(
                source=fetcher.source_name,
                articles_count=len(articles),
                status='success',
                started_at=started_at,
            )
            return articles
        except Exception as e:
            self._save_fetch_log(
                source=fetcher.source_name,
                articles_count=0,
                status='failed',
                error_message=str(e),
                started_at=started_at,
            )
            logger.error(f"[{fetcher.source_name}] Fetch failed: {e}")
            return []

    def _save_fetch_log(self, source: str, articles_count: int, status: str,
                        started_at: datetime, error_message: Optional[str] = None):
        """记录抓取日志到数据库"""
        from web.database import SessionLocal
        from web.models import FetchLog

        db = SessionLocal()
        try:
            log = FetchLog(
                source=source,
                articles_count=articles_count,
                status=status,
                error_message=error_message,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            db.add(log)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"FetchLog save error: {e}")
        finally:
            db.close()

    def _save_to_db(self, article):
        """保存文章到数据库"""
        from web.database import SessionLocal
        from web.models import Article as ArticleModel

        db = SessionLocal()
        try:
            if db.query(ArticleModel).filter(ArticleModel.url == article.url).first():
                return
            record = ArticleModel(
                url=article.url,
                title=article.title,
                summary=article.summary or '',
                content=article.content or '',
                source=article.source,
                category=article.category,
                author=(article.author or 'Unknown')[:10000],
                image_url=article.image_url or '',
                slug=make_slug(article.source, article.title),
                is_rewritten=article.rewritten,
                published_at=article.published,
            )
            db.add(record)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"DB save error: {e}")
        finally:
            db.close()

    def _is_in_db(self, url: str) -> bool:
        """检查文章是否已在数据库中"""
        from web.database import SessionLocal
        from web.models import Article as ArticleModel

        db = SessionLocal()
        try:
            return db.query(ArticleModel).filter(ArticleModel.url == url).first() is not None
        finally:
            db.close()

    def _cleanup_expired_articles(self, days: int = 7):
        """清理过期文章"""
        from web.database import SessionLocal
        from web.models import Article as ArticleModel

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        db = SessionLocal()
        try:
            deleted = db.query(ArticleModel).filter(ArticleModel.published_at < cutoff).delete()
            db.commit()
            logger.info(f"Cleaned up {deleted} expired articles")
        except Exception as e:
            db.rollback()
            logger.error(f"Cleanup error: {e}")
        finally:
            db.close()

    def _build_static_site(self):
        """构建静态站点"""
        from fetcher.site_builder import SiteBuilder

        project_root = Path(__file__).parent.parent
        output_dir = Path(os.getenv("DIST_DIR", str(project_root / "dist")))
        builder = SiteBuilder(
            content_dir=project_root / "shared-content",
            output_dir=output_dir,
            use_db=True
        )
        success = builder.build_all()
        if success:
            logger.info("Static site built successfully")
        else:
            logger.error("Failed to build static site")

    async def run_continuously(self):
        """持续运行调度器"""
        logger.info(f"Scheduler started, interval={self.interval_minutes}min")
        while True:
            await self.fetch_and_process()
            await asyncio.sleep(self.interval_minutes * 60)


async def main():
    import sys
    interval = int(os.getenv("UPDATE_INTERVAL", "15"))
    scheduler = FetchScheduler(interval_minutes=interval)

    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--once":
            await scheduler.fetch_and_process()
        else:
            await scheduler.run_continuously()
    finally:
        await scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

### 5.6 静态站点构建模块

```python
# fetcher/site_builder.py

import os
import re
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import jinja2
import markdown
from loguru import logger

SITE_TITLE = os.getenv("SITE_TITLE", "Global News Digest")
SITE_BASEURL = os.getenv("SITE_BASEURL", "")
ARTICLES_PER_PAGE = 20


class SiteBuilder:
    """Jinja2 静态站点构建器 — 从数据库或 Markdown 文件生成静态 HTML"""

    def __init__(self, content_dir: str | Path, output_dir: str | Path,
                 max_workers: int | None = None, use_db: bool = True):
        self.content_dir = Path(content_dir)
        self.output_dir = Path(output_dir)
        self.site_baseurl = SITE_BASEURL
        if not self.site_baseurl:
            logger.warning("SITE_BASEURL is empty; RSS and Sitemap will contain relative URLs. "
                           "Set SITE_BASEURL for production deployments.")
        self.max_workers = max_workers or min(16, (os.cpu_count() or 4) + 2)
        self.use_db = use_db
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(
                str(Path(__file__).parent.parent / "shared" / "templates" / "jinja2")
            ),
            autoescape=jinja2.select_autoescape(["html"]),
        )

    def _load_from_db(self) -> dict[str, list[dict]]:
        """从数据库读取文章，按分类组织"""
        from web.database import SessionLocal
        from web.models import Article

        sections: dict[str, list[dict]] = {}
        db = SessionLocal()
        try:
            articles = db.query(Article).order_by(Article.published_at.desc()).all()
            for a in articles:
                category = a.category or "uncategorized"
                slug = a.slug or f"article-{a.id}"
                date = a.published_at or a.created_at or datetime.now(timezone.utc)

                sections.setdefault(category, []).append({
                    "title": a.title or "Untitled",
                    "summary": a.summary or "",
                    "content": a.content or "",
                    "content_html": markdown.markdown(a.content, extensions=["tables", "fenced_code"])
                                    if a.content else "",
                    "category": category,
                    "source": a.source or "",
                    "source_url": a.url or "",
                    "author": a.author or "Unknown",
                    "image": a.image_url or "",
                    "date": date,
                    "published_iso": date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "published_display": f"{calendar.month_name[date.month]} {date.day}, {date.year}",
                    "permalink": f"{self.site_baseurl}/{category}/{slug}/",
                    "slug": slug,
                    "section": category,
                    "rewritten": a.is_rewritten or False,
                })

            for cat in sections:
                sections[cat].sort(key=lambda a: a["date"], reverse=True)
        finally:
            db.close()
        return sections

    def _load_from_files(self) -> dict[str, list[dict]]:
        """从 Markdown 文件读取文章（兼容旧数据）"""
        sections: dict[str, list[dict]] = {}
        if not self.content_dir.exists():
            return sections

        for category_dir in self.content_dir.iterdir():
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            for md_file in sorted(category_dir.glob("*.md"), reverse=True):
                text = md_file.read_text(encoding="utf-8")
                title_match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
                title = title_match.group(1) if title_match else md_file.stem
                slug = md_file.stem
                date = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)

                sections.setdefault(category, []).append({
                    "title": title,
                    "summary": "",
                    "content": text,
                    "content_html": markdown.markdown(text, extensions=["tables", "fenced_code"]),
                    "category": category,
                    "source": "",
                    "source_url": "",
                    "author": "Unknown",
                    "image": "",
                    "date": date,
                    "published_iso": date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "published_display": f"{calendar.month_name[date.month]} {date.day}, {date.year}",
                    "permalink": f"{self.site_baseurl}/{category}/{slug}/",
                    "slug": slug,
                    "section": category,
                    "rewritten": False,
                })

        return sections

    def build_all(self) -> bool:
        """多线程构建完整站点"""
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            sections = self._load_from_db() if self.use_db else self._load_from_files()

            if not sections:
                (self.output_dir / "index.html").write_text(
                    "<html><body><h1>No articles yet</h1></body></html>"
                )
                return True

            # 多线程渲染
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for section_name, articles in sections.items():
                    for article in articles:
                        futures.append(
                            executor.submit(self._render_article_page, section_name, article, sections)
                        )
                    total_pages = max(1, (len(articles) + ARTICLES_PER_PAGE - 1) // ARTICLES_PER_PAGE)
                    for page_num in range(1, total_pages + 1):
                        futures.append(
                            executor.submit(self._render_section_page, section_name, articles,
                                            page_num, total_pages, sections)
                        )

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Thread error: {e}")

            # 顺序构建首页、Sitemap、RSS
            self.build_homepage(sections)
            self.build_sitemap(sections)
            self.build_rss(sections)
            
            # 标记文章为已构建
            if self.use_db:
                self._mark_articles_as_built(sections)
                
            return True
        except Exception as e:
            logger.error(f"Build failed: {e}")
            return False
            
    def _mark_articles_as_built(self, sections: dict[str, list[dict]]):
        """标记所有文章为已构建"""
        from web.database import SessionLocal
        from web.models import Article
        
        db = SessionLocal()
        try:
            # 获取所有文章的 slug
            slugs = []
            for articles in sections.values():
                for article in articles:
                    if article.get("slug"):
                        slugs.append(article["slug"])
            
            if slugs:
                # 批量更新
                db.query(Article).filter(Article.slug.in_(slugs)).update(
                    {"is_built": True},
                    synchronize_session=False
                )
                db.commit()
                logger.info(f"Marked {len(slugs)} articles as built")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to mark articles as built: {e}")
        finally:
            db.close()

    def _render_article_page(self, section_name: str, article: dict, sections: dict):
        """渲染单篇文章页"""
        template = self.env.get_template("single.html")
        out_dir = self.output_dir / section_name / article["slug"]
        out_dir.mkdir(parents=True, exist_ok=True)

        ctx = {
            "site_title": SITE_TITLE,
            "page": article,
            "title": article["title"],
            "permalink": article["permalink"],
            "sections": {k: {"count": len(v)} for k, v in sections.items()},
        }
        (out_dir / "index.html").write_text(template.render(**ctx), encoding="utf-8")

    def _render_section_page(self, section_name: str, articles: list[dict],
                             page_num: int, total_pages: int, sections: dict):
        """渲染分类列表页"""
        template = self.env.get_template("list.html")
        start = (page_num - 1) * ARTICLES_PER_PAGE
        end = start + ARTICLES_PER_PAGE
        page_articles = articles[start:end]

        if page_num == 1:
            out_dir = self.output_dir / section_name
        else:
            out_dir = self.output_dir / section_name / "page" / str(page_num)
        out_dir.mkdir(parents=True, exist_ok=True)

        ctx = {
            "site_title": SITE_TITLE,
            "section_name": section_name,
            "articles": page_articles,
            "page": page_num,
            "total_pages": total_pages,
            "has_prev": page_num > 1,
            "has_next": page_num < total_pages,
            "sections": {k: {"count": len(v)} for k, v in sections.items()},
        }
        (out_dir / "index.html").write_text(template.render(**ctx), encoding="utf-8")

    def build_homepage(self, sections: dict):
        """构建首页"""
        all_articles = sorted(
            [a for arts in sections.values() for a in arts],
            key=lambda a: a["date"], reverse=True
        )
        template = self.env.get_template("index.html")
        ctx = {
            "site_title": SITE_TITLE,
            "hero_article": all_articles[0] if all_articles else None,
            "hero_side_articles": all_articles[1:4],
            "latest_articles": all_articles[4:10],
            "sections": {k: {"count": len(v)} for k, v in sections.items()},
        }
        (self.output_dir / "index.html").write_text(template.render(**ctx), encoding="utf-8")

    def build_sitemap(self, sections: dict):
        """生成 sitemap.xml"""
        urlset = Element("urlset")
        urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")
        for articles in sections.values():
            for article in articles:
                url_el = SubElement(urlset, "url")
                SubElement(url_el, "loc").text = article["permalink"]
                SubElement(url_el, "lastmod").text = article["published_iso"]

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(urlset, encoding="unicode")
        (self.output_dir / "sitemap.xml").write_text(xml_str, encoding="utf-8")

    def build_rss(self, sections: dict):
        """生成 RSS feed"""
        all_articles = sorted(
            [a for arts in sections.values() for a in arts],
            key=lambda a: a["date"], reverse=True
        )[:50]

        from email.utils import format_datetime

        items = []
        for a in all_articles:
            pub_date = format_datetime(a["date"])
            items.append(
                f'  <item>\n'
                f'    <title>{_escape_xml(a["title"])}</title>\n'
                f'    <link>{_escape_xml(a["permalink"])}</link>\n'
                f'    <description>{_escape_xml(a["summary"] or "")}</description>\n'
                f'    <pubDate>{pub_date}</pubDate>\n'
                f'  </item>'
            )

        rss = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0">\n<channel>\n'
            f'  <title>{_escape_xml(SITE_TITLE)}</title>\n'
            f'  <link>{_escape_xml(self.site_baseurl)}/</link>\n'
            f'  <description>{_escape_xml(SITE_TITLE)} - News Aggregator</description>\n'
            f'{chr(10).join(items)}\n'
            '</channel>\n</rss>'
        )
        (self.output_dir / "index.xml").write_text(rss, encoding="utf-8")


def _escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
```

### 5.7 Web 服务模块

#### 5.7.1 FastAPI 主应用

```python
# web/app.py

import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from web.routes import subscribe, subscription, admin

app = FastAPI(title="News Aggregator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(subscribe.router)
app.include_router(subscription.router)
app.include_router(admin.router)

DIST_DIR = Path(os.getenv("DIST_DIR", str(Path(__file__).parent.parent / "dist")))


@app.get("/", response_class=HTMLResponse)
async def root():
    """首页 — 返回静态站点 HTML"""
    main_index = DIST_DIR / "index.html"
    if main_index.exists():
        return HTMLResponse(content=main_index.read_text(encoding="utf-8"))
    return {"message": "News Aggregator API", "version": "1.0.0"}


ADMIN_DIR = Path(__file__).parent.parent / "admin"


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """管理后台页面"""
    admin_index = ADMIN_DIR / "index.html"
    if admin_index.exists():
        return HTMLResponse(content=admin_index.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Admin page not found")


if DIST_DIR.exists():
    app.mount("/dist", StaticFiles(directory=str(DIST_DIR), html=True), name="dist")
```

#### 5.7.2 数据库连接

```python
# web/database.py

import os
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "news_aggregator")

SQLALCHEMY_DATABASE_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

from web.models import Base  # noqa: E402


def get_db():
    """FastAPI 依赖注入 — 获取数据库 Session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

#### 5.7.3 ORM 模型

```python
# web/models.py

from datetime import datetime, timezone
from sqlalchemy import Column, BigInteger, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.mysql import JSON, MEDIUMTEXT
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Article(Base):
    __tablename__ = 'articles'

    id = Column(BigInteger, primary_key=True)
    url = Column(String(768), unique=True, nullable=False)
    title = Column(String(1024), nullable=False)
    summary = Column(Text, default='')
    content = Column(MEDIUMTEXT, default='')
    source = Column(String(255), nullable=False)
    category = Column(String(100))
    author = Column(Text, default='Unknown')
    image_url = Column(String(1024), default='')
    slug = Column(String(256), default='')
    is_rewritten = Column(Boolean, default=False)
    is_built = Column(Boolean, default=False)
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(BigInteger, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    frequency = Column(Enum('daily', 'weekly'), default='daily')
    site = Column(String(100))
    categories = Column(JSON)
    confirm_token = Column(String(64))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    confirmed_at = Column(DateTime)


class EmailLog(Base):
    __tablename__ = 'email_logs'

    id = Column(BigInteger, primary_key=True)
    subscription_id = Column(BigInteger, ForeignKey('subscriptions.id', ondelete='SET NULL'))
    email_type = Column(Enum('confirmation', 'welcome', 'digest', 'unsubscribe'), nullable=False)
    status = Column(Enum('sent', 'failed', 'bounced'), default='sent')
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FetchLog(Base):
    __tablename__ = 'fetch_logs'

    id = Column(BigInteger, primary_key=True)
    source = Column(String(255), nullable=False)
    articles_count = Column(Integer, default=0)
    status = Column(Enum('success', 'failed', 'partial'), default='success')
    error_message = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

#### 5.7.4 订阅 API

```python
# web/routes/subscribe.py

import secrets
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session
from web.database import get_db
from web.models import Subscription
from web.email_service import EmailService

router = APIRouter(prefix="/api/subscribe", tags=["subscription"])


def _get_email_service() -> EmailService:
    """懒加载 EmailService 实例（支持运行时环境变量更新和测试替换）"""
    if not hasattr(_get_email_service, '_instance'):
        _get_email_service._instance = EmailService()
    return _get_email_service._instance


class SubscribeRequest(BaseModel):
    email: EmailStr
    frequency: str = "daily"
    site: Optional[str] = None
    categories: List[str] = []

    @field_validator('frequency')
    @classmethod
    def validate_frequency(cls, v):
        if v not in ('daily', 'weekly'):
            raise ValueError('frequency must be daily or weekly')
        return v


@router.post("/")
async def subscribe(request: SubscribeRequest, db: Session = Depends(get_db)):
    """创建订阅 → 发送确认邮件"""
    existing = db.query(Subscription).filter(Subscription.email == request.email).first()

    if existing:
        if existing.is_active:
            raise HTTPException(status_code=400, detail="Already subscribed")
        existing.is_active = True
        existing.confirm_token = secrets.token_urlsafe(32)
        db.commit()
        await _get_email_service().send_confirmation_email(
            existing.email, existing.confirm_token, subscription_id=existing.id)
        return existing

    subscription = Subscription(
        email=request.email,
        frequency=request.frequency,
        site=request.site,
        categories=request.categories,
        confirm_token=secrets.token_urlsafe(32),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    await _get_email_service().send_confirmation_email(
        subscription.email, subscription.confirm_token, subscription_id=subscription.id)
    return subscription


@router.get("/confirm/{token}")
async def confirm_subscription(token: str, db: Session = Depends(get_db)):
    """确认订阅 → 发送欢迎邮件"""
    subscription = db.query(Subscription).filter(Subscription.confirm_token == token).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Invalid token")

    if subscription.confirmed_at:
        return {"status": "already_confirmed", "email": subscription.email}

    subscription.confirmed_at = datetime.now(timezone.utc)
    db.commit()

    categories = subscription.categories if subscription.categories else []
    await _get_email_service().send_welcome_email(
        subscription.email, categories, subscription_id=subscription.id)
    return {"status": "confirmed", "email": subscription.email}


@router.delete("/{email}")
async def unsubscribe(
    email: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """取消订阅（需 token 或邮件验证）"""
    subscription = db.query(Subscription).filter(Subscription.email == email).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Not found")

    if token and subscription.confirm_token == token:
        pass
    elif not token:
        raise HTTPException(
            status_code=401,
            detail="Verification required. Provide token parameter or request a verification email."
        )
    else:
        raise HTTPException(status_code=403, detail="Invalid token")

    subscription.is_active = False
    db.commit()
    await _get_email_service().send_unsubscribe_confirmation(
        subscription.email, subscription_id=subscription.id)
    return {"status": "unsubscribed"}
```

#### 5.7.5 管理 API

```python
# web/routes/admin.py

import os
import hmac
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func
from sqlalchemy.orm import Session
from web.database import get_db
from web.models import Article, Subscription, FetchLog, EmailLog

router = APIRouter(prefix="/api/admin", tags=["admin"])
security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """HTTP Basic Auth 验证 — 使用恒定时间比较防止时序攻击"""
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if not admin_password:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD not set")

    if not (hmac.compare_digest(credentials.username, admin_username)
            and hmac.compare_digest(credentials.password, admin_password)):
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@router.get("/stats")
async def get_stats(admin: str = Depends(verify_credentials), db: Session = Depends(get_db)):
    """站点统计（需认证）"""
    return {
        "total_articles": db.query(Article).count(),
        "total_subscriptions": db.query(Subscription).count(),
        "active_subscriptions": db.query(Subscription).filter(Subscription.is_active == True).count(),
        "rewritten_articles": db.query(Article).filter(Article.is_rewritten == True).count(),
        "unrewritten_articles": db.query(Article).filter(Article.is_rewritten == False).count(),
        "built_articles": db.query(Article).filter(Article.is_built == True).count(),
        "unbuilt_articles": db.query(Article).filter(Article.is_built == False).count(),
    }


@router.get("/articles")
async def list_articles(
    source: Optional[str] = None,
    is_built: Optional[bool] = None,
    is_rewritten: Optional[bool] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(verify_credentials),
    db: Session = Depends(get_db),
):
    """文章列表（支持筛选和分页，需认证）"""
    query = db.query(Article)
    if source:
        query = query.filter(Article.source == source)
    if is_built is not None:
        query = query.filter(Article.is_built == is_built)
    if is_rewritten is not None:
        query = query.filter(Article.is_rewritten == is_rewritten)

    total = query.count()
    articles = query.order_by(Article.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": a.id,
                "url": a.url,
                "title": a.title,
                "source": a.source,
                "category": a.category,
                "slug": a.slug,
                "is_rewritten": a.is_rewritten,
                "is_built": a.is_built,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in articles
        ],
    }


@router.delete("/articles/{article_id}")
async def delete_article(
    article_id: int,
    admin: str = Depends(verify_credentials),
    db: Session = Depends(get_db),
):
    """删除文章（需认证）"""
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    md_file_deleted = False
    if article.slug:
        md_path = Path(os.getenv("CONTENT_DIR", "./shared-content")) / (article.category or "uncategorized") / f"{article.slug}.md"
        if md_path.exists():
            md_path.unlink()
            md_file_deleted = True

    db.delete(article)
    db.commit()
    return {"status": "deleted", "md_file_deleted": md_file_deleted}


@router.get("/articles/sources")
async def get_article_sources(
    admin: str = Depends(verify_credentials),
    db: Session = Depends(get_db),
):
    """按来源统计文章数（需认证）"""
    results = db.query(Article.source, func.count(Article.id)).group_by(Article.source).all()
    return [{"source": r[0], "count": r[1]} for r in results]


@router.get("/articles/{article_id}")
async def get_article(
    article_id: int,
    admin: str = Depends(verify_credentials),
    db: Session = Depends(get_db),
):
    """文章详情（需认证）"""
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    md_path = Path(os.getenv("CONTENT_DIR", "./shared-content")) / (article.category or "uncategorized") / f"{article.slug}.md"
    return {
        "id": article.id,
        "url": article.url,
        "title": article.title,
        "source": article.source,
        "category": article.category,
        "slug": article.slug,
        "is_rewritten": article.is_rewritten,
        "is_built": article.is_built,
        "has_md_file": md_path.exists(),
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "created_at": article.created_at.isoformat() if article.created_at else None,
    }


@router.post("/articles/cleanup")
async def cleanup_articles(
    days: int = Query(7, ge=1, le=90),
    admin: str = Depends(verify_credentials),
    db: Session = Depends(get_db),
):
    """清理过期文章（需认证）"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    articles = db.query(Article).filter(Article.published_at < cutoff).all()

    removed_files = 0
    content_dir = Path(os.getenv("CONTENT_DIR", "./shared-content"))
    for article in articles:
        if article.slug:
            md_path = content_dir / (article.category or "uncategorized") / f"{article.slug}.md"
            if md_path.exists():
                md_path.unlink()
                removed_files += 1

    removed_db = len(articles)
    for article in articles:
        db.delete(article)
    db.commit()

    return {"removed_files": removed_files, "removed_db": removed_db}


@router.get("/fetch/logs")
async def get_fetch_logs(
    source: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(verify_credentials),
    db: Session = Depends(get_db),
):
    """抓取日志（分页，需认证）"""
    query = db.query(FetchLog)
    if source:
        query = query.filter(FetchLog.source == source)
    total = query.count()
    logs = query.order_by(FetchLog.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": log.id,
                "source": log.source,
                "articles_count": log.articles_count,
                "status": log.status,
                "error_message": log.error_message,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }


@router.get("/subscriptions")
async def list_subscriptions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(verify_credentials),
    db: Session = Depends(get_db),
):
    """订阅用户列表（分页，需认证）"""
    total = db.query(Subscription).count()
    subscriptions = db.query(Subscription).order_by(Subscription.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": s.id,
                "email": s.email,
                "is_active": s.is_active,
                "frequency": s.frequency,
                "site": s.site,
                "categories": s.categories if s.categories else [],
                "confirmed_at": s.confirmed_at.isoformat() if s.confirmed_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in subscriptions
        ],
    }


@router.get("/email/logs")
async def get_email_logs(
    email_type: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(verify_credentials),
    db: Session = Depends(get_db),
):
    """邮件发送日志（分页，需认证）"""
    query = db.query(EmailLog)
    if email_type:
        query = query.filter(EmailLog.email_type == email_type)
    if status:
        query = query.filter(EmailLog.status == status)
    total = query.count()
    logs = query.order_by(EmailLog.sent_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": log.id,
                "subscription_id": log.subscription_id,
                "email_type": log.email_type,
                "status": log.status,
                "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            }
            for log in logs
        ],
    }


_build_status = {"status": "idle"}
_build_lock = threading.Lock()


@router.post("/build")
async def trigger_build(admin: str = Depends(verify_credentials)):
    """触发站点构建（后台线程，需认证）"""
    global _build_status
    with _build_lock:
        if _build_status.get("status") == "building":
            raise HTTPException(status_code=409, detail="构建正在进行中，请稍后再试")
        _build_status = {"status": "building", "started_at": datetime.now(timezone.utc).isoformat()}

    from fetcher.site_builder import SiteBuilder

    project_root = Path(__file__).parent.parent.parent
    output_dir = Path(os.getenv("DIST_DIR", str(project_root / "dist")))

    def _build():
        global _build_status
        try:
            builder = SiteBuilder(project_root / "shared-content", output_dir, use_db=True)
            success = builder.build_all()
            from web.database import SessionLocal
            from web.models import Article
            db = SessionLocal()
            try:
                articles_count = db.query(Article).count()
            finally:
                db.close()
            _build_status = {
                "status": "success" if success else "failed",
                "started_at": _build_status["started_at"],
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "message": "站点构建成功" if success else "站点构建失败",
                "articles_count": articles_count,
            }
        except Exception as e:
            _build_status = {
                "status": "failed",
                "started_at": _build_status["started_at"],
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "message": str(e),
            }

    thread = threading.Thread(target=_build, daemon=True)
    thread.start()
    return {"status": "started", "message": "站点构建已启动"}


@router.get("/build/status")
async def get_build_status(
    admin: str = Depends(verify_credentials),
):
    """查询构建状态（需认证）"""
    with _build_lock:
        return dict(_build_status)
```

#### 5.7.6 订阅管理 API

```python
# web/routes/subscription.py

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from web.database import get_db
from web.models import Subscription

router = APIRouter(prefix="/api/subscriptions", tags=["subscription-management"])


class SubscriptionUpdateRequest(BaseModel):
    frequency: Optional[str] = None
    categories: Optional[List[str]] = None

    @field_validator('frequency')
    @classmethod
    def validate_frequency(cls, v):
        if v is not None and v not in ('daily', 'weekly'):
            raise ValueError('frequency must be daily or weekly')
        return v


@router.get("/{email}")
async def get_subscription(
    email: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """查询订阅详情（需 token 或邮件验证）"""
    subscription = db.query(Subscription).filter(Subscription.email == email).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Not found")

    if token and subscription.confirm_token == token:
        pass
    elif not token:
        raise HTTPException(
            status_code=401,
            detail="Verification required. Provide token parameter or request a verification email."
        )
    else:
        raise HTTPException(status_code=403, detail="Invalid token")

    return {
        "email": subscription.email,
        "is_active": subscription.is_active,
        "frequency": subscription.frequency,
        "site": subscription.site,
        "categories": subscription.categories if subscription.categories else [],
        "confirmed_at": subscription.confirmed_at.isoformat() if subscription.confirmed_at else None,
    }


@router.patch("/{email}")
async def update_subscription(
    email: str,
    request: SubscriptionUpdateRequest,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """更新订阅偏好（需 token 或邮件验证）"""
    subscription = db.query(Subscription).filter(Subscription.email == email).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Not found")

    if token and subscription.confirm_token == token:
        pass
    elif not token:
        raise HTTPException(
            status_code=401,
            detail="Verification required. Provide token parameter or request a verification email."
        )
    else:
        raise HTTPException(status_code=403, detail="Invalid token")

    if request.frequency is not None:
        subscription.frequency = request.frequency
    if request.categories is not None:
        subscription.categories = request.categories

    db.commit()
    return {"status": "updated"}
```

#### 5.7.7 邮件服务

```python
# web/email_service.py

import os
from typing import Optional, List
from loguru import logger


class EmailService:
    """邮件发送服务 — 基于 SendGrid"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SENDGRID_API_KEY")
        self.from_email = os.getenv("EMAIL_FROM", "noreply@newsaggregator.com")
        self.enabled = bool(self.api_key)
        self._sg = None

    @property
    def sg(self):
        """懒加载 SendGrid 客户端（复用连接）"""
        if self._sg is None and self.enabled:
            from sendgrid import SendGridAPIClient
            self._sg = SendGridAPIClient(self.api_key)
        return self._sg

    async def send_email(self, to_email: str, subject: str, html_content: str,
                         subscription_id: Optional[int] = None, email_type: Optional[str] = None) -> bool:
        """发送邮件 — SendGrid 同步 API 包装为异步，发送后记录 EmailLog"""
        if not self.enabled:
            logger.warning(f"[Email] Service not enabled, skipping: {to_email}")
            self._save_email_log(subscription_id, email_type, 'failed')
            return False

        try:
            import asyncio
            from sendgrid.helpers.mail import Mail

            message = Mail(
                from_email=self.from_email,
                to_emails=to_email,
                subject=subject,
                html_content=html_content,
            )

            response = await asyncio.to_thread(self.sg.send, message)

            if response.status_code in [200, 201, 202]:
                logger.info(f"[Email] Sent to {to_email}")
                self._save_email_log(subscription_id, email_type, 'sent')
                return True
            self._save_email_log(subscription_id, email_type, 'failed')
            return False
        except Exception as e:
            logger.error(f"[Email] Error: {e}")
            self._save_email_log(subscription_id, email_type, 'failed')
            return False

    def _save_email_log(self, subscription_id: Optional[int], email_type: Optional[str], status: str):
        """记录邮件发送日志到数据库"""
        if not email_type:
            return
        try:
            from web.database import SessionLocal
            from web.models import EmailLog

            db = SessionLocal()
            try:
                log = EmailLog(
                    subscription_id=subscription_id,
                    email_type=email_type,
                    status=status,
                )
                db.add(log)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"EmailLog save error: {e}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"EmailLog save error: {e}")

    async def send_confirmation_email(self, email: str, token: str,
                                       subscription_id: Optional[int] = None,
                                       site_url: Optional[str] = None) -> bool:
        """发送订阅确认邮件"""
        base_url = site_url or os.getenv("SITE_BASEURL", "https://localhost:8000")
        confirm_url = f"{base_url}/api/subscribe/confirm/{token}"
        html = f"""
        <html><body style="font-family: Arial; max-width: 600px; margin: 0 auto;">
            <h2>Confirm Your Subscription</h2>
            <p>Click the button below to confirm:</p>
            <a href="{confirm_url}" style="background: #e94560; color: white;
               padding: 12px 24px; text-decoration: none; border-radius: 4px;">
               Confirm Subscription</a>
        </body></html>
        """
        return await self.send_email(email, "Confirm Your Subscription", html,
                                     subscription_id=subscription_id, email_type='confirmation')

    async def send_welcome_email(self, email: str, categories: Optional[List[str]] = None,
                                 subscription_id: Optional[int] = None) -> bool:
        """发送欢迎邮件"""
        cats = ", ".join(categories) if categories else "all categories"
        html = f"""
        <html><body style="font-family: Arial; max-width: 600px; margin: 0 auto;">
            <h2>Welcome!</h2>
            <p>Your subscription is confirmed. You'll receive news from {cats}.</p>
        </body></html>
        """
        return await self.send_email(email, "Welcome to News Aggregator!", html,
                                     subscription_id=subscription_id, email_type='welcome')

    async def send_unsubscribe_confirmation(self, email: str,
                                            subscription_id: Optional[int] = None) -> bool:
        """发送取消订阅确认"""
        html = """
        <html><body style="font-family: Arial; max-width: 600px; margin: 0 auto;">
            <h2>Unsubscribed</h2>
            <p>Your subscription has been cancelled.</p>
        </body></html>
        """
        return await self.send_email(email, "Unsubscribed Successfully", html,
                                     subscription_id=subscription_id, email_type='unsubscribe')
```

---

## 6. 配置管理

### 6.1 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MYSQL_HOST` | `localhost` | MySQL 主机 |
| `MYSQL_PORT` | `3306` | MySQL 端口 |
| `MYSQL_USER` | `root` | MySQL 用户 |
| `MYSQL_PASSWORD` | — | MySQL 密码 |
| `MYSQL_DATABASE` | `news_aggregator` | 数据库名 |
| `ADMIN_USERNAME` | `admin` | 管理员用户名 |
| `ADMIN_PASSWORD` | — | 管理员密码（必填） |
| `LLM_API_KEY` | — | LLM API 密钥 |
| `LLM_BASE_URL` | — | LLM API 地址 |
| `LLM_MODEL` | `gpt-3.5-turbo` | LLM 模型名 |
| `SENDGRID_API_KEY` | — | SendGrid API 密钥 |
| `EMAIL_FROM` | `noreply@newsaggregator.com` | 发件人地址 |
| `CONTENT_DIR` | `./shared-content` | 稿件目录 |
| `DIST_DIR` | `./dist` | 静态站点输出目录 |
| `UPDATE_INTERVAL` | `15` | 抓取间隔（分钟） |
| `CONTENT_ENRICHMENT` | `true` | 是否启用内容充实 |
| `ENRICHMENT_CONCURRENT` | `3` | 充实并发数 |
| `SITE_TITLE` | `Global News Digest` | 站点标题 |
| `SITE_BASEURL` | — | 站点基础 URL |
| `CORS_ORIGINS` | `*` | CORS 允许的源（逗号分隔，生产环境应设为具体域名） |

### 6.2 Docker Compose 服务编排

```yaml
# docker-compose.yml

version: '3.8'

services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: news_aggregator
    volumes:
      - mysql_data:/var/lib/mysql
      - ./database/schema.sql:/docker-entrypoint-initdb.d/schema.sql
    ports:
      - "3306:3306"
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - news-network

  fetcher:
    build:
      context: .
      dockerfile: Dockerfile.fetcher
    depends_on:
      mysql:
        condition: service_healthy
    environment:
      - MYSQL_HOST=mysql
      - MYSQL_PASSWORD=${MYSQL_ROOT_PASSWORD}
      - LLM_API_KEY=${LLM_API_KEY}
      - LLM_BASE_URL=${LLM_BASE_URL}
      - LLM_MODEL=${LLM_MODEL:-gpt-3.5-turbo}
      - SITE_BASEURL=${SITE_BASEURL:-}
    volumes:
      - ./shared-content:/app/shared-content
      - ./dist:/app/dist
    networks:
      - news-network

  web:
    build:
      context: .
      dockerfile: Dockerfile.web
    depends_on:
      mysql:
        condition: service_healthy
    ports:
      - "8000:8000"
    environment:
      - MYSQL_HOST=mysql
      - MYSQL_PASSWORD=${MYSQL_ROOT_PASSWORD}
      - ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
      - ADMIN_PASSWORD=${ADMIN_PASSWORD}
      - SENDGRID_API_KEY=${SENDGRID_API_KEY}
      - EMAIL_FROM=${EMAIL_FROM:-noreply@newsaggregator.com}
      - CORS_ORIGINS=${CORS_ORIGINS:-*}
      - LLM_API_KEY=${LLM_API_KEY}
      - LLM_BASE_URL=${LLM_BASE_URL}
      - LLM_MODEL=${LLM_MODEL:-gpt-3.5-turbo}
      - SITE_BASEURL=${SITE_BASEURL:-}
    volumes:
      - ./shared-content:/app/shared-content
      - ./dist:/app/dist
    networks:
      - news-network

volumes:
  mysql_data:

networks:
  news-network:
    driver: bridge
```

---

## 7. CI/CD 流水线

```yaml
# .github/workflows/deploy.yml

name: Build and Deploy

on:
  push:
    branches: [main]
  schedule:
    - cron: '*/15 * * * *'

jobs:
  build:
    runs-on: ubuntu-latest

    services:
      mysql:
        image: mysql:8.0
        env:
          MYSQL_ROOT_PASSWORD: test_password
          MYSQL_DATABASE: news_aggregator
        ports:
          - 3306:3306
        options: >-
          --health-cmd="mysqladmin ping -h localhost"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=5

    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r fetcher/requirements.txt
          pip install -r web/requirements.txt

      - name: Fetch content
        run: python -m fetcher.scheduler --once
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          MYSQL_HOST: ${{ secrets.MYSQL_HOST }}
          MYSQL_PASSWORD: ${{ secrets.MYSQL_PASSWORD }}

      - name: Build static site
        run: python -c "import os; from fetcher.site_builder import SiteBuilder; from pathlib import Path; SiteBuilder(Path('shared-content'), Path(os.getenv('DIST_DIR', 'dist')), use_db=True).build_all()"

      - name: Deploy
        run: bash shared/scripts/deploy.sh
        env:
          REMOTE_HOST: ${{ secrets.REMOTE_HOST }}
          REMOTE_USER: ${{ secrets.REMOTE_USER }}
          SSH_KEY: ${{ secrets.SSH_KEY }}
```

---

## 8. 关键技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 静态站点生成 | Jinja2 替代 Hugo | 更灵活的内容处理，直接从 DB 读取，无需中间 Markdown |
| 去重算法 | SimHash | 适合大规模文本近似去重，汉明距离计算高效 |
| HTTP 客户端 | httpx（抓取）+ curl_cffi（充实） | httpx 原生 async；curl_cffi 模拟浏览器指纹绕过反爬 |
| 正文提取 | trafilatura | 专为新闻文章设计，高召回率，无需写 CSS 选择器 |
| LLM 接入 | OpenAI 兼容协议 | 一套代码支持所有主流 LLM 服务商 |
| 邮件服务 | SendGrid | 免费 100 封/天，API 简单，无需自建 SMTP |
| ORM | SQLAlchemy 2.0 | Python 生态最成熟的 ORM，支持异步扩展 |
| Web 框架 | FastAPI | 原生 async，自动 OpenAPI 文档，类型安全 |
| 容器化 | Docker Compose | 单机部署简单，服务编排清晰 |
