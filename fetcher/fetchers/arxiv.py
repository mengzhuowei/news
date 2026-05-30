from .base import BaseFetcher, Article
from loguru import logger


class ArxivFetcher(BaseFetcher):
    """arXiv 抓取器 — 覆盖 AI/ML/NLP/CV/Economics 分类"""

    RSS_URLS = {
        'artificial-intelligence': 'https://rss.arxiv.org/rss/cs.AI',
        'machine-learning': 'https://rss.arxiv.org/rss/cs.LG',
        'nlp': 'https://rss.arxiv.org/rss/cs.CL',
        'computer-vision': 'https://rss.arxiv.org/rss/cs.CV',
        'economics': 'https://rss.arxiv.org/rss/econ.GN',
    }

    def __init__(self):
        super().__init__(
            source_name="arXiv",
            source_url="https://arxiv.org",
            category="research",
        )

    async def fetch(self) -> list[Article]:
        all_articles = []
        for category, rss_url in self.RSS_URLS.items():
            articles = await self.fetch_rss(rss_url)
            for article in articles:
                article.category = category
            all_articles.extend(articles)

        logger.info(f"[arXiv] Total fetched: {len(all_articles)} articles")
        return all_articles
