from .base import BaseFetcher, Article
from loguru import logger


class EconomistFetcher(BaseFetcher):
    """The Economist 抓取器"""

    # 主 /rss 端点返回 403，使用分类 RSS 代替
    RSS_URLS = {
        "finance": "https://www.economist.com/finance-and-economics/rss.xml",
        "science": "https://www.economist.com/science-and-technology/rss.xml",
        "world": "https://www.economist.com/international/rss.xml",
        "business": "https://www.economist.com/business/rss.xml",
        "united-states": "https://www.economist.com/united-states/rss.xml",
    }

    def __init__(self):
        super().__init__(
            source_name="The Economist",
            source_url="https://www.economist.com",
            category="finance",
        )

    async def fetch(self) -> list[Article]:
        all_articles = []
        for category, rss_url in self.RSS_URLS.items():
            articles = await self.fetch_rss(rss_url)
            for article in articles:
                article.category = category
            all_articles.extend(articles)
        logger.info(f"[Economist] Total fetched: {len(all_articles)} articles")
        return all_articles
