# 新闻聚合平台 — 产品设计文档（PRD）

> 版本：2.0 | 最后更新：2026-05-29

---

## 1. 产品概述

### 1.1 产品定位

面向全球读者的多源新闻聚合平台，自动从 7+ 权威新闻源抓取内容，经 AI 清洗与重写后生成 SEO 优化的静态站点，同时提供邮件订阅推送服务。

### 1.2 核心价值

| 价值维度 | 说明 |
|----------|------|
| 内容聚合 | 一站式获取 BBC、Reuters、Bloomberg 等 7+ 源的新闻 |
| AI 增强 | 自动去重、清洗、摘要生成、内容重写，提升内容质量 |
| SEO 优化 | 静态站点 + Sitemap + RSS + 结构化数据，搜索引擎友好 |
| 邮件订阅 | 支持按分类、频率订阅，自动推送新闻摘要 |

### 1.3 目标用户

| 用户角色 | 需求场景 |
|----------|----------|
| 普通读者 | 快速浏览多源新闻，订阅感兴趣的分类 |
| 研究人员 | 跟踪 arXiv 最新论文，获取 AI/ML/CV 领域动态 |
| 投资者 | 聚合 Bloomberg/FT/Reuters 财经资讯 |
| 站点管理员 | 管理抓取源、审核内容、查看运营数据 |

---

## 2. 功能模块

### 2.1 数据抓取层

#### 2.1.1 多源 RSS 抓取

| 新闻源 | 分类 | RSS 地址 |
|--------|------|----------|
| BBC News | world / technology / business / science | `feeds.bbci.co.uk/news/{category}/rss.xml` |
| Reuters | world / business / technology / markets | `reutersagency.com/feed/?best-topics` |
| Bloomberg | markets / technology / business / world | `feeds.bloomberg.com/{category}/news.rss` |
| Financial Times | finance | `ft.com/rss/home` |
| TechCrunch | startups / tech | `techcrunch.com/feed/` |
| The Economist | finance | `economist.com/rss` |
| arXiv | AI / ML / NLP / CV / Economics | `rss.arxiv.org/rss/{category_code}` |

**功能要点**：
- 异步并发抓取所有源，`asyncio.gather` + `return_exceptions` 保证单源失败不影响全局
- 每个 RSS 条目提取：标题、链接、正文、摘要、发布时间、作者、图片
- 图片提取优先级：`media:content` → `media:thumbnail` → `enclosures` → HTML `<img>`

#### 2.1.2 内容充实（Content Enrichment）

当 RSS 条目仅包含摘要而缺少全文时，自动回源抓取网页正文：

- 使用 `curl_cffi` 模拟浏览器指纹（Chrome/Safari/Firefox），绕过基础反爬
- 使用 `trafilatura` 从 HTML 中提取正文，优先召回率（`favor_recall=True`）
- 按域名限速（默认 2 秒间隔），信号量控制并发（默认 3）

#### 2.1.3 定时调度

- 默认每 15 分钟执行一次完整抓取流程
- 支持 `--once` 参数单次运行（CI/CD 场景）
- 流程：抓取 → 充实 → 去重 → 清洗 → 洗稿 → 入库 → 清理过期 → 构建站点

### 2.2 内容处理层

#### 2.2.1 去重检测（SimHash）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `hash_bits` | 64 | SimHash 哈希位数 |
| `duplicate_threshold` | 0.8 | 相似度阈值，超过则判定为重复 |

**去重策略**：
1. 精确匹配：URL 去重（Aggregator 层）+ 标题小写去重 + SimHash 精确匹配
2. 模糊匹配：遍历已见哈希集合，计算汉明距离，相似度 ≥ 阈值则判定重复

#### 2.2.2 内容清洗

| 清洗步骤 | 说明 |
|----------|------|
| HTML 标签移除 | 移除 `<script>`/`<style>` 及所有 HTML 标签，解码 HTML 实体 |
| 广告移除 | 正则匹配订阅推广、Cookie/隐私政策等文本 |
| 文本规范化 | 合并多余空白，移除特殊字符 |
| 内容截断 | 超过 5000 字符时在句号处截断 |
| 版权声明 | 附加来源链接和版权声明 |

#### 2.2.3 AI 摘要与重写

**摘要生成**：
- 无 API Key 时：取首句截断至 200 字符（fallback）
- 有 API Key 时：调用 LLM 生成 200 字符以内的摘要

**内容重写（洗稿）**：
- 第一次尝试：标准重写 Prompt（保留事实，改写句式）
- 失败后重试：安全 Prompt（中立客观语气，避免敏感内容）
- 仍失败则返回原文
- 重写后额外调用 LLM 生成摘要

**支持的 LLM 服务商**：

| 服务商 | Base URL | 推荐模型 |
|--------|----------|----------|
| OpenAI | `https://api.openai.com/v1` | gpt-4o |
| DeepSeek | `https://api.deepseek.com/v1` | deepseek-chat |
| 阿里云 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-turbo |
| 火山引擎 | `https://ark.cn-beijing.volces.com/api/v3` | doubao-pro-32k |
| Ollama 本地 | `http://localhost:11434/v1` | llama3.1 |

### 2.3 静态站点生成层

#### 2.3.1 Jinja2 模板渲染

| 页面类型 | 模板文件 | 说明 |
|----------|----------|------|
| 首页 | `index.html` | Hero 文章 + 侧栏 + 最新列表 + 更多 |
| 分类列表页 | `list.html` | 分页展示，每页 20 篇 |
| 文章详情页 | `single.html` | 正文 + 来源信息 + 版权声明 |
| Sitemap | 自动生成 | XML 格式，包含所有文章 URL |
| RSS Feed | 自动生成 | 最近 50 篇文章 |

#### 2.3.2 多线程构建

- 使用 `ThreadPoolExecutor` 并行渲染文章页和列表页
- 首页、Sitemap、RSS 顺序生成（依赖全量数据）
- 构建完成后将文章标记为 `is_built=True`

#### 2.3.3 数据来源

- 优先从 MySQL 数据库读取（`use_db=True`）
- 回退到 Markdown 文件读取（兼容旧数据）

### 2.4 Web 服务层

#### 2.4.1 公开 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /` | GET | 首页（返回静态站点 HTML） |
| `GET /admin` | GET | 管理后台页面 |

#### 2.4.2 订阅 API

| 端点 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/subscribe/` | POST | 创建订阅 | 无 |
| `/api/subscribe/confirm/{token}` | GET | 确认订阅 | Token |
| `/api/subscribe/{email}` | DELETE | 取消订阅 | Token/邮件验证 |
| `/api/subscriptions/{email}` | GET | 查询订阅详情 | Token/邮件验证 |
| `/api/subscriptions/{email}` | PATCH | 更新订阅偏好 | Token/邮件验证 |

**订阅流程**：
1. 用户提交邮箱 + 偏好 → 创建订阅记录（含 confirm_token）→ 发送确认邮件
2. 用户点击确认链接 → 标记 `confirmed_at` → 发送欢迎邮件
3. 用户取消订阅/更新偏好 → 需通过邮件验证或使用订阅时生成的token

**安全说明**：查询、修改和取消订阅需通过确认 token 或重新发送验证邮件后操作

#### 2.4.3 管理 API

| 端点 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/admin/stats` | GET | 站点统计 | HTTP Basic |
| `/api/admin/articles` | GET | 文章列表（分页+筛选） | HTTP Basic |
| `/api/admin/articles/{id}` | GET | 文章详情 | HTTP Basic |
| `/api/admin/articles/{id}` | DELETE | 删除文章 | HTTP Basic |
| `/api/admin/articles/cleanup` | POST | 清理过期文章 | HTTP Basic |
| `/api/admin/articles/sources` | GET | 按来源统计 | HTTP Basic |
| `/api/admin/fetch/logs` | GET | 抓取日志 | HTTP Basic |
| `/api/admin/subscriptions` | GET | 订阅用户列表 | HTTP Basic |
| `/api/admin/email/logs` | GET | 邮件发送日志 | HTTP Basic |
| `/api/admin/build` | POST | 触发站点构建 | HTTP Basic |
| `/api/admin/build/status` | GET | 构建状态 | HTTP Basic |

### 2.5 邮件服务层

| 邮件类型 | 触发时机 | 内容 |
|----------|----------|------|
| 确认邮件 | 新订阅创建 | 含确认链接的按钮 |
| 欢迎邮件 | 订阅确认后 | 订阅分类摘要 + 后续说明 |
| 每日摘要 | 定时推送（V2.1 规划） | 最近 10 篇新闻标题+摘要 |
| 取消确认 | 用户取消订阅 | 重新订阅入口 |

**邮件服务商**：SendGrid（通过 API Key 配置）

---

## 3. 非功能需求

### 3.1 性能

| 指标 | 目标 |
|------|------|
| 单次全量抓取耗时 | < 60 秒（7 源并发） |
| 静态站点构建耗时 | < 30 秒（1000 篇文章） |
| API 响应时间 | < 200ms（P95） |
| 并发订阅请求 | 支持 100 QPS |

### 3.2 可用性

| 指标 | 目标 |
|------|------|
| 服务可用率 | 99.9% |
| 单源抓取失败 | 不影响其他源 |
| 数据库故障 | 自动重连（`pool_pre_ping=True`） |
| LLM 服务不可用 | 降级为基础摘要 |

**监控与告警**：
- 服务健康检查：定期检查各服务状态
- 错误告警：关键错误（数据库错误、邮件发送失败）触发告警
- 指标收集：抓取成功率、文章数量、订阅数量等运营指标

**备份与恢复**：
- 数据库定期备份（每日）
- 配置文件版本化管理
- 静态内容保留最近3个版本

### 3.3 安全

| 要求 | 说明 |
|------|------|
| 管理接口认证 | HTTP Basic Auth |
| 密码存储 | 环境变量，不硬编码 |
| SQL 注入防护 | SQLAlchemy ORM 参数化查询 |
| XSS 防护 | Jinja2 自动转义 |
| CORS | 限制为前端域名 |

### 3.4 可扩展性

| 维度 | 扩展方式 |
|------|----------|
| 新增新闻源 | 继承 `BaseFetcher`，实现 `fetch()` 方法 |
| 新增 LLM 服务商 | 配置 `LLM_BASE_URL` + `LLM_MODEL` |
| 新增站点 | 添加 Jinja2 模板 |
| 新增邮件服务商 | 实现 `EmailService` 接口 |

---

## 4. 用户故事

### US-01：读者浏览新闻
> 作为读者，我想访问首页查看最新新闻，以便快速了解全球动态。

**验收标准**：
- 首页展示 Hero 文章 + 侧栏推荐 + 最新列表
- 文章按发布时间倒序排列
- 点击文章可查看详情页

### US-02：读者按分类浏览
> 作为读者，我想按分类（科技/财经/世界等）浏览新闻，以便聚焦感兴趣的领域。

**验收标准**：
- 分类列表页支持分页（每页 20 篇）
- URL 格式：`/{category}/` 和 `/{category}/page/{n}/`

### US-03：读者订阅新闻
> 作为读者，我想通过邮件订阅特定分类的新闻，以便定期接收更新。

**验收标准**：
- 提交邮箱后收到确认邮件
- 点击确认链接完成订阅
- 可选择订阅频率（daily/weekly）和分类

### US-04：管理员查看统计
> 作为管理员，我想查看站点统计数据，以便了解运营状况。

**验收标准**：
- 展示文章总数、订阅数、重写率、构建率等指标
- 需要管理员认证

### US-05：管理员管理文章
> 作为管理员，我想查看、筛选和删除文章，以便控制站点内容质量。

**验收标准**：
- 支持按来源、构建状态、重写状态筛选
- 支持分页浏览
- 删除文章同时删除关联的 Markdown 文件

### US-06：管理员触发构建
> 作为管理员，我想手动触发站点构建，以便在内容更新后立即发布。

**验收标准**：
- 构建在后台线程执行，不阻塞 API
- 可查询构建状态和进度
- 构建中时拒绝重复触发（409）

### US-07：管理员查看抓取日志
> 作为管理员，我想查看抓取日志，以便排查抓取问题。

**验收标准**：
- 日志包含抓取源、状态、错误信息、文章数量
- 支持按来源筛选和分页
- 需要管理员认证

### US-08：读者接收邮件摘要
> 作为订阅用户，我想定期收到新闻摘要邮件，以便及时获取更新。

**验收标准**：
- 按订阅频率（daily/weekly）发送摘要
- 摘要包含订阅分类下的最新文章标题和链接
- 包含一键退订链接

### US-09：SEO优化
> 作为内容运营者，我想站点具备SEO友好功能，以便被搜索引擎更好地索引。

**验收标准**：
- 生成 sitemap.xml
- 生成 RSS feed
- 页面包含合理的 meta 标签和结构化数据

---

## 5. 数据流转

```
RSS 源
  │
  ▼
┌──────────────┐
│  BaseFetcher  │  (RSS 解析)
│              │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Aggregator   │  (抓取器管理，收集所有源文章)
│              │
└──────┬───────┘
       │ List[Article]
       ▼
┌──────────────┐
│ ContentEnricher│  (RSS 无全文时回源抓取)
│ (curl_cffi +  │
│  trafilatura) │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ ContentCleaner│  (SimHash 去重 + HTML 清洗 + 广告移除 + 截断)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Summarizer   │  (AI 摘要 + AI 重写)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   MySQL DB    │  (articles 表)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  SiteBuilder  │  (Jinja2 多线程渲染 → dist/)
└──────┬───────┘
       │
       ▼
┌──────────────────────┐     ┌───────────────┐
│  FastAPI Web (静态+API)  │     │  EmailService  │
└──────────────────────┘     │  (SendGrid)    │
                             └───────────────┘
```

---

## 6. 发布计划

| 阶段 | 功能 | 状态 |
|------|------|------|
| V1.0 | RSS 抓取 + 基础清洗 + Hugo 静态站点 | ✅ 已完成 |
| V1.5 | AI 摘要/重写 + MySQL 持久化 + Jinja2 构建 | ✅ 已完成 |
| V2.0 | 邮件订阅 + 管理后台 + 内容充实 + Docker 部署 | ✅ 已完成 |
| V2.1 | 安全加固 + 性能优化 + 测试完善 | 🔄 规划中 |
| V3.0 | 多语言支持 + 用户系统 + 评论系统 | 📋 待规划 |

**V2.1 详细规划**：
- 安全加固：所有管理API认证、订阅操作验证、XSS/CSRF防护、请求限流
- 性能优化：SimHash去重优化、构建缓存、数据库索引优化
- 测试完善：单元测试、集成测试、压力测试
- 监控告警：服务健康检查、错误告警、指标收集
- 备份策略：数据库定期备份、配置版本管理
- 每日摘要：实现邮件定时推送功能

