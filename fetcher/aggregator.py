"""新闻聚合调度器 — 管理所有抓取器实例"""

from fetcher.fetchers.bbc import BBCFetcher
from fetcher.fetchers.reuters import ReutersFetcher
from fetcher.fetchers.bloomberg import BloombergFetcher
from fetcher.fetchers.ft import FTWatcher
from fetcher.fetchers.techcrunch import TechCrunchFetcher
from fetcher.fetchers.economist import EconomistFetcher
from fetcher.fetchers.arxiv import ArxivFetcher
from fetcher.fetchers.nytimes import NYTFetcher
from fetcher.fetchers.yahoo import YahooNewsFetcher


class Aggregator:
    """新闻聚合调度器 — 管理所有抓取器实例"""

    def __init__(self):
        self.fetchers = [
            BBCFetcher(),
            ReutersFetcher(),
            BloombergFetcher(),
            FTWatcher(),
            TechCrunchFetcher(),
            EconomistFetcher(),
            ArxivFetcher(),
            NYTFetcher(),
            YahooNewsFetcher(),
        ]

    async def close_all(self):
        for fetcher in self.fetchers:
            try:
                await fetcher.close()
            except Exception:
                pass
