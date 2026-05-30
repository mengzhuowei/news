import re
from urllib.parse import urlparse, unquote

from .base import BaseFetcher, Article
from loguru import logger


class ReutersFetcher(BaseFetcher):
    """Reuters 抓取器 — 通过 Google News RSS 代理获取"""

    # 原 reutersagency.com/feed 已于 2024 年下线，改用 Google News RSS 代理
    RSS_URL = "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en"

    # 按关键词粗分分类
    _CATEGORY_KEYWORDS = {
        "technology": ["ai ", "artificial intelligence", "chip", "tech", "semiconductor",
                        "software", "cyber", "robot", "quantum", "blockchain"],
        "business": ["market", "stock", "trade", "econom", "financ", "bank",
                      "invest", "profit", "revenue", "gdp", "inflation"],
        "world": ["war", "military", "election", "president", "minister",
                   "diplomat", "sanction", "nato", "un ", "summit"],
    }

    def __init__(self):
        super().__init__(
            source_name="Reuters",
            source_url="https://www.reuters.com",
            category="news",
        )

    def _guess_category(self, title: str, description: str) -> str:
        """根据标题和摘要粗分分类"""
        text = f"{title} {description}".lower()
        for cat, keywords in self._CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return cat
        return "world"

    def _clean_google_title(self, title: str) -> str:
        """清除 Google News 标题中的来源后缀，如 '... - Reuters'"""
        return re.sub(r'\s*-\s*Reuters\s*$', '', title).strip()

    def _resolve_google_url(self, google_url: str) -> str:
        """尝试从 Google News 跳转链接中提取真实 Reuters URL"""
        # Google News RSS 链接格式: https://news.google.com/rss/articles/CBMi...
        # 跟随重定向可获取真实 URL，但为避免额外请求，直接返回 Google 链接
        # content_enricher 会跟随重定向获取正文
        return google_url

    async def fetch(self) -> list[Article]:
        articles = await self.fetch_rss(self.RSS_URL)
        for article in articles:
            article.source = "Reuters"
            article.title = self._clean_google_title(article.title)
            article.category = self._guess_category(article.title, article.summary)
        logger.info(f"[Reuters] Total fetched: {len(articles)} articles")
        return articles
