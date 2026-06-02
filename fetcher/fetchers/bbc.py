from .base import BaseFetcher, Article
from loguru import logger


class BBCFetcher(BaseFetcher):
    """BBC News 抓取器 — 从 11 个分类 RSS 源并发抓取"""

    RSS_URLS = {
        'top': 'https://feeds.bbci.co.uk/news/rss.xml',
        'world': 'https://feeds.bbci.co.uk/news/world/rss.xml',
        'uk': 'https://feeds.bbci.co.uk/news/uk/rss.xml',
        'technology': 'https://feeds.bbci.co.uk/news/technology/rss.xml',
        'business': 'https://feeds.bbci.co.uk/news/business/rss.xml',
        'health': 'https://feeds.bbci.co.uk/news/health/rss.xml',
        'culture': 'https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml',
        'science': 'https://feeds.bbci.co.uk/news/science_and_environment/rss.xml',
        'politics': 'https://feeds.bbci.co.uk/news/politics/rss.xml',
        'education': 'https://feeds.bbci.co.uk/news/education/rss.xml',
        'sport': 'https://feeds.bbci.co.uk/sport/rss.xml',
    }

    def __init__(self):
        super().__init__(
            source_name="BBC News",
            source_url="https://www.bbc.com/news",
            category="news",
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
