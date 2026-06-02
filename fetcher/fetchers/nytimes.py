from .base import BaseFetcher, Article
from loguru import logger


class NYTFetcher(BaseFetcher):
    """New York Times 抓取器 — 从 12 个分类 RSS 源并发抓取"""

    RSS_URLS = {
        'top': 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml',
        'world': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
        'us': 'https://rss.nytimes.com/services/xml/rss/nyt/US.xml',
        'politics': 'https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml',
        'business': 'https://rss.nytimes.com/services/xml/rss/nyt/Business.xml',
        'technology': 'https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml',
        'science': 'https://rss.nytimes.com/services/xml/rss/nyt/Science.xml',
        'health': 'https://rss.nytimes.com/services/xml/rss/nyt/Health.xml',
        'sport': 'https://rss.nytimes.com/services/xml/rss/nyt/Sports.xml',
        'culture': 'https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml',
        'climate': 'https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml',
        'education': 'https://rss.nytimes.com/services/xml/rss/nyt/Education.xml',
    }

    def __init__(self):
        super().__init__(
            source_name="New York Times",
            source_url="https://www.nytimes.com",
            category="news",
        )

    async def fetch(self) -> list[Article]:
        all_articles = []
        for category, rss_url in self.RSS_URLS.items():
            articles = await self.fetch_rss(rss_url)
            for article in articles:
                article.category = category
            all_articles.extend(articles)

        logger.info(f"[NYT] Total fetched: {len(all_articles)} articles")
        return all_articles