import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from loguru import logger

# RSS 域名级限速：同一域名两次请求间隔至少 3 秒，防止并发打同一源被封
_RSS_DOMAIN_DELAY: dict[str, float] = {}
_RSS_MIN_INTERVAL = 3.0


@dataclass
class Article:
    """文章数据结构 — 贯穿抓取、清洗、存储全流程"""
    title: str
    url: str
    content: str
    summary: str
    published: datetime
    source: str
    category: str
    author: Optional[str] = None
    image_url: Optional[str] = None
    language: str = "en"
    rewritten: bool = False
    # 多语言翻译结果
    translations: dict = field(default_factory=dict)


class BaseFetcher(ABC):
    """抓取器抽象基类 — 所有新闻源抓取器必须继承此类"""

    def __init__(self, source_name: str, source_url: str, category: str):
        self.source_name = source_name
        self.source_url = source_url
        self.category = category
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        """懒加载 httpx 客户端，避免在事件循环外创建"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                },
            )
        return self._client

    async def fetch_rss(self, rss_url: str) -> List[Article]:
        """通用 RSS 抓取方法（含域名级限速）"""
        # 域名限速：同一域名间隔至少 _RSS_MIN_INTERVAL 秒
        domain = urlparse(rss_url).netloc
        last_time = _RSS_DOMAIN_DELAY.get(domain, 0)
        wait = max(0, _RSS_MIN_INTERVAL - (time.monotonic() - last_time))
        if wait > 0:
            await asyncio.sleep(wait)
        _RSS_DOMAIN_DELAY[domain] = time.monotonic()

        try:
            response = await self.client.get(rss_url)
            response.raise_for_status()
            feed = feedparser.parse(response.text)

            articles = []
            for entry in feed.entries:
                article = self._parse_rss_entry(entry)
                if article:
                    articles.append(article)

            logger.info(f"[{self.source_name}] Fetched {len(articles)} from RSS")
            return articles
        except Exception as e:
            logger.error(f"[{self.source_name}] RSS fetch failed: {e}")
            return []

    def _parse_rss_entry(self, entry) -> Optional[Article]:
        """解析单个 RSS 条目为 Article 对象"""
        from email.utils import parsedate_to_datetime
        try:
            title = entry.get('title', '')
            url = entry.get('link', '')
            summary = entry.get('summary', entry.get('description', ''))
            published_str = entry.get('published', entry.get('updated', ''))

            try:
                published = parsedate_to_datetime(published_str)
            except Exception:
                published = datetime.now(timezone.utc)

            content = summary
            if 'content' in entry:
                content = entry.content[0].value

            return Article(
                title=title,
                url=url,
                content=content,
                summary=self._clean_text(summary),
                published=published,
                source=self.source_name,
                category=self.category,
                author=entry.get('author', ''),
                image_url=self._extract_image(entry),
            )
        except Exception as e:
            logger.error(f"[{self.source_name}] Parse error: {e}")
            return None

    def _clean_text(self, text: str) -> str:
        """清洗 HTML 标签，返回纯文本"""
        text = re.sub(r'<[^>]+>', '', text)
        return text.strip()

    def _extract_image(self, entry) -> Optional[str]:
        """从 RSS 条目提取图片，按优先级：media:content → media:thumbnail → enclosures → HTML <img>"""
        if 'media_content' in entry:
            for media in entry.media_content:
                media_url = media.get('url', '')
                if media.get('medium') == 'image' or media_url.lower().endswith(
                    ('.jpg', '.jpeg', '.png', '.webp')
                ):
                    return media_url or None

        if 'media_thumbnail' in entry and entry.media_thumbnail:
            return entry.media_thumbnail[0].get('url')

        if 'enclosures' in entry and entry.enclosures:
            for enc in entry.enclosures:
                if enc.get('type', '').startswith('image/'):
                    return enc.get('href') or enc.get('url')

        for field_name in ('content', 'summary', 'description'):
            html = entry.get(field_name, '')
            if isinstance(html, list) and html:
                html = html[0].get('value', '')
            if html:
                soup = BeautifulSoup(html, 'lxml')
                img = soup.find('img')
                if img and img.get('src', '').startswith(('http://', 'https://')):
                    return img['src']

        return None

    @abstractmethod
    async def fetch(self) -> List[Article]:
        """子类必须实现：执行抓取并返回文章列表"""
        pass

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
