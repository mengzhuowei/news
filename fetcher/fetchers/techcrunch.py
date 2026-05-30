from .base import BaseFetcher, Article
from loguru import logger


class TechCrunchFetcher(BaseFetcher):
    """TechCrunch 抓取器"""

    def __init__(self):
        super().__init__(
            source_name="TechCrunch",
            source_url="https://techcrunch.com",
            category="technology",
        )

    async def fetch(self) -> list[Article]:
        articles = await self.fetch_rss("https://techcrunch.com/feed/")
        for article in articles:
            article.category = "technology"
        logger.info(f"[TechCrunch] Total fetched: {len(articles)} articles")
        return articles
