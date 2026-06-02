from .base import BaseFetcher, Article
from loguru import logger


class FTWatcher(BaseFetcher):
    """Financial Times 抓取器 — 从 5 个分类 RSS 源并发抓取"""

    # /rss/home 已 301 重定向至 /rss/home/international
    RSS_URLS = {
        'finance': 'https://www.ft.com/rss/home/international',
        'world': 'https://www.ft.com/rss/world',
        'companies': 'https://www.ft.com/rss/companies',
        'markets': 'https://www.ft.com/rss/markets',
        'technology': 'https://www.ft.com/rss/technology',
    }

    def __init__(self):
        super().__init__(
            source_name="Financial Times",
            source_url="https://www.ft.com",
            category="finance",
        )

    async def fetch(self) -> list[Article]:
        all_articles = []
        for category, rss_url in self.RSS_URLS.items():
            articles = await self.fetch_rss(rss_url)
            for article in articles:
                article.category = category
            all_articles.extend(articles)

        logger.info(f"[FT] Total fetched: {len(all_articles)} articles")
        return all_articles
