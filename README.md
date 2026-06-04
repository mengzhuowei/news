# News Aggregator — 全球新闻聚合平台

> 自动从 7+ 权威新闻源抓取内容,经 AI 清洗与重写后生成 SEO 友好的静态站点,并通过 Email / Telegram 推送给订阅用户。

---

## 目录

- [项目概述](#项目概述)
- [核心特性](#核心特性)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [目录结构](#目录结构)
- [数据模型](#数据模型)
- [数据流转](#数据流转)
- [快速开始](#快速开始)
- [环境变量](#环境变量)
- [操作命令](#操作命令)
- [API 概览](#api-概览)
- [部署与 CI/CD](#部署与-cicd)
- [测试](#测试)

---

## 项目概述

News Aggregator 是一个面向全球读者的多源新闻聚合与分发系统,提供:

| 维度 | 能力 |
|------|------|
| 内容聚合 | BBC、Reuters、Bloomberg、FT、TechCrunch、The Economist、arXiv 等 7+ 源 |
| AI 处理 | SimHash 去重、HTML 清洗、广告移除、AI 摘要、AI 重写(洗稿) |
| 站点生成 | Jinja2 多线程渲染 → 静态 HTML / RSS / Sitemap |
| 推送通道 | Email(SendGrid)+ Telegram Bot 定时推送 |
| 后台管理 | Web 管理界面 + 完整 REST API,支持文章、订阅、抓取日志、邮件日志 |

详细产品设计见 [docs/product-design.md](docs/product-design.md),架构设计见 [docs/architecture-design.md](docs/architecture-design.md)。

---

## 核心特性

- **多源并发抓取**:asyncio 异步并发抓取,单源失败不影响全局
- **内容充实**:RSS 缺全文时自动回源,curl_cffi 模拟浏览器指纹,trafilatura 提取正文
- **AI 增强**:兼容 OpenAI 协议的 LLM(OpenAI / DeepSeek / 通义千问 / Ollama 等),支持摘要 + 重写
- **多线程构建**:ThreadPoolExecutor 并行渲染文章页与列表页
- **定时调度**:每 15 分钟一次完整抓取流程,可配置
- **双通道推送**:SendGrid 邮件 + Telegram Bot,按分类、频率、时区订阅
- **HTTPS 一键部署**:内置 certbot 自动签发 Let's Encrypt 证书
- **安全认证**:管理 API HTTP Basic Auth,订阅操作 Token 验证,密码恒定时间比较

---

## 技术栈

| 层 | 选型 |
|----|------|
| 抓取 | httpx · feedparser · curl_cffi · trafilatura · beautifulsoup4 |
| 处理 | asyncio · numpy(SimHash)· loguru |
| AI | openai 兼容协议(任意 OpenAI 兼容端点) |
| 数据库 | MySQL 8.0 + SQLAlchemy 2.0 + PyMySQL |
| Web | FastAPI + Uvicorn + Jinja2 + Pydantic v2 |
| 邮件 | SendGrid |
| IM | python-telegram-bot |
| 站点生成 | Jinja2 · ThreadPoolExecutor |
| 容器 | Docker · Docker Compose |
| 反向代理 | Nginx + Certbot(Let's Encrypt) |
| CI/CD | GitHub Actions |

---

## 系统架构

### 服务拓扑

```
                        ┌──────────────────────────┐
                        │    User / Telegram Bot   │
                        └────────┬────────┬────────┘
                          HTTPS   │        │
                                 ▼        ▼
                       ┌─────────────────────────┐
                       │   Nginx :80/:443        │
                       │   (TLS + 反代 + 静态)    │
                       └────────┬────────────────┘
                                │ /api/* /subscribe /search
                                ▼
                       ┌─────────────────────────┐
                       │   FastAPI Web :8000     │
                       │   (静态站点 + API)       │
                       └────────┬────────────────┘
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
         ┌──────────┐    ┌──────────┐     ┌──────────┐
         │  MySQL   │    │SendGrid  │     │  LLM API │
         │  :3306   │    │ (邮件)    │     │OpenAI兼容│
         └────▲─────┘    └──────────┘     └──────────┘
              │
   ┌──────────┴──────────┐         ┌──────────────────┐
   │   Fetcher Service   │         │  Push Scheduler  │
   │  (抓取+处理+构建)    │         │ (Telegram 定时)  │
   └─────────────────────┘         └──────────────────┘
              ▲
   ┌──────────┴──────────┐
   │  Telegram Bot       │
   │  (订阅/偏好命令)     │
   └─────────────────────┘
```

### Docker Compose 服务清单

| 服务 | 镜像 / 构建 | 端口 | 说明 |
|------|------------|------|------|
| `mysql` | mysql:8.0 | 3306 (仅本机) | 数据持久化,启动时自动执行 `schema.sql` |
| `fetcher` | `Dockerfile.fetcher` | — | RSS 抓取、内容处理、站点构建循环 |
| `web` | `Dockerfile.web` | 8000 | FastAPI API + 静态首页 |
| `telegram-bot` | `Dockerfile.web` | — | Telegram Bot 长连接 |
| `push-scheduler` | `Dockerfile.fetcher` | — | 定时扫描订阅,发送 Telegram 推送 |
| `nginx` | nginx:stable | 80 / 443 | 反向代理 + 静态站点托管 + TLS |
| `certbot` | certbot/certbot | — | 自动签发与续期 Let's Encrypt 证书 |

所有服务通过 `news-network` 桥接网络互通。

---

## 目录结构

```
news/
├── fetcher/                       # 数据抓取与处理层
│   ├── aggregator.py              # 抓取器聚合(7 个新闻源)
│   ├── cleaner.py                 # SimHash 去重 + 内容清洗
│   ├── content_enricher.py        # RSS 缺全文时回源抓取
│   ├── summarizer.py              # AI 摘要 + AI 重写
│   ├── site_builder.py            # Jinja2 静态站点构建
│   ├── scheduler.py               # 抓取调度主循环
│   ├── push_scheduler.py          # Telegram 推送调度
│   ├── backfill_rewrite.py        # 历史数据批量重写
│   ├── requirements.txt
│   └── fetchers/                  # 各新闻源抓取器
│       ├── base.py                # BaseFetcher + Article 数据类
│       ├── bbc.py / reuters.py / bloomberg.py
│       ├── ft.py / techcrunch.py / economist.py
│       ├── nytimes.py / yahoo.py / arxiv.py
│       └── __init__.py
│
├── web/                           # Web 服务层
│   ├── app.py                     # FastAPI 主应用
│   ├── database.py                # SQLAlchemy Engine + Session
│   ├── models.py                  # ORM 模型
│   ├── email_service.py           # SendGrid 邮件服务
│   ├── telegram_service.py        # Telegram 消息发送
│   ├── telegram_bot.py            # Telegram Bot 命令处理
│   ├── requirements.txt
│   ├── requirements-core.txt
│   └── routes/
│       ├── auth.py                # Token 验证工具
│       ├── validators.py          # Pydantic 验证器
│       ├── subscribe.py           # 订阅 API
│       ├── subscription.py        # 订阅管理 API
│       ├── telegram_subscribe.py  # Telegram 订阅 API
│       ├── search.py              # 站内搜索 API + 页面
│       └── admin.py               # 管理后台 API
│
├── shared/                        # 共享资源
│   ├── scripts/
│   │   ├── build.sh               # 抓取+构建脚本
│   │   └── deploy.sh              # rsync 部署脚本
│   ├── static/                    # 静态资源
│   └── templates/jinja2/          # Jinja2 模板
│       ├── base.html / index.html / list.html
│       ├── single.html / search.html
│
├── database/                      # 数据库
│   ├── schema.sql                 # 完整 Schema
│   └── migrations/                # 增量迁移
│       ├── add_email_unique_index.sql
│       ├── add_fulltext_index.sql
│       └── add_telegram_fields.sql
│
├── admin/
│   └── index.html                 # 管理后台前端
│
├── tests/
│   └── test_core.py               # 单元测试
│
├── nginx/                         # Nginx + Certbot
│   ├── conf.d/default.conf.template
│   ├── entrypoint.sh
│   └── certbot-entrypoint.sh
│
├── docs/
│   ├── architecture-design.md
│   └── product-design.md
│
├── .github/workflows/deploy.yml   # CI/CD
├── Dockerfile.fetcher
├── Dockerfile.web
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 数据模型

完整 SQL 见 [database/schema.sql](database/schema.sql)。

### ER 关系

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────┐
│   articles   │       │  subscriptions   │       │  email_logs  │
├──────────────┤       ├──────────────────┤       ├──────────────┤
│ id (PK)      │       │ id (PK)          │◄──────│ id (PK)      │
│ url (UQ)     │       │ email (UQ)       │       │ subscription │
│ title        │       │ chat_id (UQ)     │       │  _id (FK)    │
│ summary      │       │ is_active        │       │ email_type   │
│ content      │       │ frequency        │       │ status       │
│ source       │       │ push_time        │       │ sent_at      │
│ category     │       │ timezone         │       └──────────────┘
│ author       │       │ push_channel     │
│ image_url    │       │ categories(JSON) │
│ slug         │       │ confirm_token    │       ┌──────────────┐
│ is_rewritten │       │ last_pushed_at   │       │  fetch_logs  │
│ is_built     │       │ created_at       │       ├──────────────┤
│ published_at │       │ confirmed_at     │       │ id (PK)      │
│ created_at   │       └──────────────────┘       │ source       │
└──────────────┘                                 │ status       │
       ▲                                          │ articles_count│
       │                                          │ error_message│
       └──────────────────────────────────────────│ started_at   │
                                                  │ completed_at │
                                                  └──────────────┘
```

### articles 关键字段

- `url` UNIQUE:业务去重依据
- `content` MEDIUMTEXT:支持最长 16MB 长文
- `author` TEXT:arXiv 作者列表可达数千字符
- `slug`:由 `make_slug(source, title, id)` 生成,用于静态文件命名
- `is_rewritten` / `is_built`:管理端筛选与构建状态追踪
- 索引:`source` / `category` / `published_at` / `is_rewritten` / `is_built` / `slug` / 全文索引 `(title, summary, content)`

### subscriptions 关键字段

- `email` / `chat_id` 至少需填一个
- `frequency`:`daily` / `weekly`
- `push_channel`:`telegram` / `email`
- `push_time`:`HH:MM:SS`
- `timezone`:IANA 时区字符串
- `categories`:JSON 数组
- `confirm_token`:`secrets.token_urlsafe(32)` 生成

### 迁移记录

| 文件 | 变更 |
|------|------|
| `add_email_unique_index.sql` | `email` 升级为 UNIQUE 索引 |
| `add_fulltext_index.sql` | `articles(title,summary,content)` 全文索引 |
| `add_telegram_fields.sql` | Telegram 推送相关字段 |

---

## 数据流转

```
RSS 源 (7+ 个)
   │
   ▼
┌──────────────┐
│  Aggregator  │  BaseFetcher:feedparser + httpx,异步并发
└──────┬───────┘
       │ List[Article]
       ▼
┌──────────────────┐
│ ContentEnricher  │  curl_cffi 模拟浏览器 + trafilatura 提取正文
│  (可选, 按域名限速) │
└──────┬───────────┘
       ▼
┌──────────────┐
│ContentCleaner│  SimHash 去重 + HTML 清洗 + 广告移除 + 截断 + 版权声明
└──────┬───────┘
       ▼
┌──────────────┐
│  Summarizer  │  LLM 重写(标准 Prompt → 安全 Prompt fallback) + AI 摘要
└──────┬───────┘
       ▼
┌──────────────┐
│   MySQL DB   │  articles / subscriptions / fetch_logs / email_logs
└──────┬───────┘
       ▼
┌──────────────┐
│ SiteBuilder  │  Jinja2 多线程渲染 → dist/index.html, /{cat}/, /{cat}/{slug}/
└──────┬───────┘       RSS feed, sitemap.xml
       ▼
┌──────────────┐         ┌──────────────┐
│  Nginx 静态   │         │ PushScheduler│
│  /api → Web  │         │  Telegram    │
└──────────────┘         └──────────────┘
                                   │
                                   ▼
                          ┌──────────────┐
                          │  Email / TG  │
                          └──────────────┘
```

---

## 快速开始

### 前置要求

- Docker 20+ 与 Docker Compose v2
- (可选)本地 Python 3.11 + MySQL 8.0

### 1. 克隆与配置

```bash
git clone <repository-url> news
cd news
cp .env.example .env
# 编辑 .env,至少填入 MYSQL_ROOT_PASSWORD 与 ADMIN_PASSWORD
```

### 2. 启动全栈

```bash
docker compose up -d
# 查看启动状态
docker compose ps
docker compose logs -f fetcher
```

服务启动后:

- API:http://localhost:8000
- 管理后台:http://localhost:8000/admin
- 订阅页:http://localhost:8000/subscribe
- 站点首页:http://localhost:8000/

### 3. 触发一次抓取(可选)

`fetcher` 容器默认每 15 分钟自动跑一次,首次启动后数据可能为空,可手动触发:

```bash
docker compose exec fetcher python -m fetcher.scheduler --once
```

### 4. 触发一次站点构建

```bash
docker compose exec fetcher python -m fetcher.site_builder
```

或调用管理 API(需 Basic Auth):

```bash
curl -u admin:YOUR_PASSWORD -X POST http://localhost:8000/api/admin/build
curl -u admin:YOUR_PASSWORD http://localhost:8000/api/admin/build/status
```

---

## 环境变量

复制自 [.env.example](.env.example),完整列表:

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `MYSQL_ROOT_PASSWORD` | ✅ | — | MySQL root 密码 |
| `MYSQL_HOST` | — | `localhost` | DB 主机(Docker 内为 `mysql`) |
| `MYSQL_PORT` | — | `3306` | DB 端口 |
| `MYSQL_USER` | — | `root` | DB 用户 |
| `MYSQL_DATABASE` | — | `news_aggregator` | 库名 |
| `DB_POOL_SIZE` | — | `10` | SQLAlchemy 连接池大小 |
| `DB_MAX_OVERFLOW` | — | `20` | 超出池大小的最大连接 |
| `DB_POOL_RECYCLE` | — | `1800` | 连接回收秒数 |
| `ADMIN_USERNAME` | — | `admin` | 管理后台用户 |
| `ADMIN_PASSWORD` | ✅ | — | 管理后台密码 |
| `LLM_API_KEY` | — | — | OpenAI 兼容 API Key(留空禁用 AI) |
| `LLM_BASE_URL` | — | — | 自定义端点,如 `https://api.deepseek.com/v1` |
| `LLM_MODEL` | — | `gpt-3.5-turbo` | 模型名 |
| `SENDGRID_API_KEY` | — | — | SendGrid 邮件 Key(留空禁用邮件) |
| `EMAIL_FROM` | — | `noreply@newsaggregator.com` | 发件邮箱 |
| `SITE_TITLE` | — | `Global News Digest` | 站点标题 |
| `SITE_BASEURL` | — | — | 站点公网 URL,影响 RSS/Sitemap/邮件链接 |
| `DIST_DIR` | — | `./dist` | 静态站点输出 |
| `CORS_ORIGINS` | — | `*` | 逗号分隔的允许来源 |
| `CERTBOT_EMAIL` | — | — | Let's Encrypt 注册邮箱 |
| `UPDATE_INTERVAL` | — | `15` | 抓取间隔(分钟) |
| `CONTENT_ENRICHMENT` | — | `false` | 是否启用内容充实 |
| `ENRICHMENT_CONCURRENT` | — | `3` | 充实并发数 |
| `ARTICLE_CLEANUP_ENABLED` | — | `false` | 是否清理过期文章 |
| `ARTICLE_RETENTION_DAYS` | — | `7` | 保留天数 |
| `TELEGRAM_BOT_TOKEN` | — | — | BotFather Token |
| `TELEGRAM_BOT_USERNAME` | — | — | Bot 用户名(订阅页跳转用) |
| `PUSH_CHECK_INTERVAL` | — | `60` | 推送检查间隔(秒) |
| `ARTICLES_PER_PUSH` | — | `10` | 每次推送文章数 |
| `DEFAULT_TIMEZONE` | — | `UTC` | 默认时区 |
| `ADSENSE_CLIENT_ID` | — | — | Google AdSense ID |
| `TWITTER_SITE` / `TWITTER_CREATOR` | — | — | Twitter Cards meta |
| `GA_ID` | — | — | Google Analytics ID |

---

## 操作命令

### 容器生命周期

```bash
# 启动全栈
docker compose up -d

# 查看运行状态
docker compose ps

# 查看实时日志(指定服务)
docker compose logs -f fetcher
docker compose logs -f web
docker compose logs -f telegram-bot
docker compose logs -f push-scheduler

# 重启单个服务
docker compose restart fetcher
docker compose restart web

# 停止并删除容器(保留数据卷)
docker compose down

# 停止并清除所有数据卷(慎用)
docker compose down -v
```

### 进入容器调试

```bash
# 进入 fetcher 容器
docker compose exec fetcher bash

# 进入 web 容器
docker compose exec web bash

# 直接连 MySQL
docker compose exec mysql mysql -uroot -p news_aggregator
```

### 抓取与构建

```bash
# 完整抓取 + 清洗 + 入库(单次)
docker compose exec fetcher python -m fetcher.scheduler --once

# 仅构建静态站点(不抓取)
docker compose exec fetcher python -m fetcher.site_builder

# 使用构建脚本(等价于上述两步)
docker compose exec fetcher bash /app/shared/scripts/build.sh
docker compose exec fetcher bash /app/shared/scripts/build.sh --build-only
docker compose exec fetcher bash /app/shared/scripts/build.sh --help
```

### Push Scheduler(推送调度)

```bash
# 手动跑一次推送(立即扫描到期订阅)
docker compose exec push-scheduler python -c "
import asyncio
from fetcher.push_scheduler import PushScheduler
asyncio.run(PushScheduler().run_once())
"
```

### 管理 API 速查

```bash
# 站点统计
curl -u admin:$ADMIN_PASSWORD http://localhost:8000/api/admin/stats

# 文章列表(分页)
curl -u admin:$ADMIN_PASSWORD "http://localhost:8000/api/admin/articles?limit=20&skip=0"

# 触发构建
curl -u admin:$ADMIN_PASSWORD -X POST http://localhost:8000/api/admin/build

# 清理 7 天前的文章
curl -u admin:$ADMIN_PASSWORD -X POST "http://localhost:8000/api/admin/articles/cleanup?days=7"

# 抓取日志
curl -u admin:$ADMIN_PASSWORD "http://localhost:8000/api/admin/fetch/logs?limit=20"
```

### 数据库迁移

```bash
# schema.sql 会在 MySQL 容器首次启动时自动执行
# 后续迁移需手动执行:
docker compose exec -T mysql mysql -uroot -p$MYSQL_ROOT_PASSWORD news_aggregator \
  < database/migrations/add_telegram_fields.sql
```

### 本地开发(无 Docker)

```bash
# 1. 启动本地 MySQL,创建 news_aggregator 库并导入 schema
mysql -uroot -p < database/schema.sql

# 2. 安装依赖
pip install -r fetcher/requirements.txt
pip install -r web/requirements.txt

# 3. 准备 .env(确保 MYSQL_HOST=localhost)

# 4. 启动 Web API
uvicorn web.app:app --reload --port 8000

# 5. 另一个终端跑抓取
python -m fetcher.scheduler --once

# 6. 构建站点
python -m fetcher.site_builder
```

### 回填 / 批量重写

```bash
# 对历史未重写文章批量重写(消耗大量 LLM 配额)
docker compose exec fetcher python -m fetcher.backfill_rewrite
```

---

## API 概览

| 模块 | 端点前缀 | 认证 |
|------|----------|------|
| 公开页面 | `GET /`、`GET /admin`、`GET /subscribe` | — |
| 搜索 | `GET /search/` | — |
| 订阅 | `POST /api/subscribe/`、`GET /api/subscribe/confirm/{token}`、`DELETE /api/subscribe/{email}` | Token |
| 订阅管理 | `GET/PATCH /api/subscriptions/{email}` | Token |
| Telegram 订阅 | `/api/telegram/...` | Token / chat_id |
| 管理后台 | `/api/admin/*` | HTTP Basic |

所有 `/api/admin/*` 端点均需 `ADMIN_USERNAME` / `ADMIN_PASSWORD`。完整参数与响应见 [docs/architecture-design.md §4](docs/architecture-design.md)。

---

## 部署与 CI/CD

### 1. 直接部署到服务器

```bash
# 在服务器上
git clone <repo> /opt/news && cd /opt/news
cp .env.example .env && vim .env   # 配置 MYSQL_PASSWORD / ADMIN_PASSWORD / LLM / Telegram 等
docker compose up -d --build
```

### 2. GitHub Actions 自动化

[.github/workflows/deploy.yml](.github/workflows/deploy.yml) 在 `push main` 时执行:

1. 启动 MySQL 服务
2. 安装 Python 依赖
3. 跑一次 `fetcher.scheduler --once` 抓取
4. 调用 `SiteBuilder` 构建静态站点
5. 通过 `shared/scripts/deploy.sh`(rsync over SSH)同步 `dist/` 到远程服务器

需要在 GitHub 仓库配置以下 Secrets:

| Secret | 用途 |
|--------|------|
| `CI_MYSQL_PASSWORD` | CI 内 MySQL 密码 |
| `LLM_API_KEY` | CI 内 LLM 密钥 |
| `REMOTE_HOST` / `REMOTE_USER` / `SSH_KEY` | 部署目标 |

### 3. HTTPS 自动签发

`nginx` 与 `certbot` 通过共享卷 `certbot-etc` / `certbot-var` / `webroot` 协作:

- 首次启动 `certbot` 容器会通过 `certbot-entrypoint.sh` 申请证书
- 之后每 12 小时自动续期
- 证书存在前 `nginx` 健康检查会失败,需等待首签完成

需在 `.env` 配置 `SITE_BASEURL=https://yourdomain.com` 与 `CERTBOT_EMAIL`。

---

## 测试

```bash
# 安装 pytest
pip install pytest pytest-asyncio

# 运行单元测试
pytest tests/ -v

# 单文件
pytest tests/test_core.py::TestSimHashEdgeCases -v
```

测试覆盖:

- `make_slug` 特殊字符与唯一性
- 验证器(`validate_push_time` / `validate_frequency`)
- Token / chat_id 验证
- Subscription 模型约束
- `_is_safe_url` SSRF 防护
- SimHash 边界(单/双词、超长、Unicode)
- ContentCleaner(script/style 移除、相似去重、广告移除)
- Summarizer 重写结果校验

---

## 许可证

本项目仅供学习与个人使用。所有抓取内容版权归原始来源所有。
