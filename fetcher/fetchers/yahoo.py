from .base import BaseFetcher, Article
from loguru import logger


class YahooNewsFetcher(BaseFetcher):
    """Yahoo News 抓取器 — 从主 RSS 源抓取"""

    RSS_URL = "https://news.yahoo.com/rss/"

    def __init__(self):
        super().__init__(
            source_name="Yahoo News",
            source_url="https://news.yahoo.com",
            category="top",
        )

    async def fetch(self) -> list[Article]:
        articles = await self.fetch_rss(self.RSS_URL)
        for article in articles:
            article.category = "top"
        logger.info(f"[Yahoo News] Total fetched: {len(articles)} articles")
        return articles