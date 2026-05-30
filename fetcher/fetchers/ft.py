from .base import BaseFetcher, Article
from loguru import logger


class FTWatcher(BaseFetcher):
    """Financial Times 抓取器"""

    # /rss/home 已 301 重定向至 /rss/home/international
    RSS_URL = "https://www.ft.com/rss/home/international"

    def __init__(self):
        super().__init__(
            source_name="Financial Times",
            source_url="https://www.ft.com",
            category="finance",
        )

    async def fetch(self) -> list[Article]:
        articles = await self.fetch_rss(self.RSS_URL)
        for article in articles:
            article.category = "finance"
        logger.info(f"[FT] Total fetched: {len(articles)} articles")
        return articles
