from .base import BaseFetcher, Article
from loguru import logger


class BloombergFetcher(BaseFetcher):
    """Bloomberg 抓取器"""

    RSS_URLS = {
        'markets': 'https://feeds.bloomberg.com/markets/news.rss',
        'technology': 'https://feeds.bloomberg.com/technology/news.rss',
        'business': 'https://feeds.bloomberg.com/business/news.rss',
        'politics': 'https://feeds.bloomberg.com/politics/news.rss',
        'economics': 'https://feeds.bloomberg.com/economics/news.rss',
        'wealth': 'https://feeds.bloomberg.com/wealth/news.rss',
    }

    def __init__(self):
        super().__init__(
            source_name="Bloomberg",
            source_url="https://www.bloomberg.com",
            category="news",
        )

    async def fetch(self) -> list[Article]:
        all_articles = []
        for category, rss_url in self.RSS_URLS.items():
            articles = await self.fetch_rss(rss_url)
            for article in articles:
                article.category = category
            all_articles.extend(articles)

        logger.info(f"[Bloomberg] Total fetched: {len(all_articles)} articles")
        return all_articles
