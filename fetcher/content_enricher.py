"""内容充实器 — 为 RSS 缺少全文的文章抓取网页正文"""

import asyncio
import ipaddress
import re
import time
from typing import Optional, List
from urllib.parse import urlparse
from loguru import logger
from fetcher.fetchers.base import Article

_DOMAIN_DELAY: dict[str, float] = {}
_DEFAULT_DELAY = 2.0

# SSRF 防护：禁止访问的私有/内部网段
_BLOCKED_NETWORKS = [
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('100.64.0.0/10'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    # 198.18.0.0/15 excluded — used by iCloud Private Relay on macOS, not a real internal network
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('::ffff:0:0/96'),
    ipaddress.ip_network('fc00::/7'),
]


def _is_safe_url(url: str) -> bool:
    """检查 URL 是否指向公网地址，防止 SSRF 攻击"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        # 检查是否为 IP 地址
        try:
            ip = ipaddress.ip_address(hostname)
            for network in _BLOCKED_NETWORKS:
                if ip in network:
                    return False
        except ValueError:
            # 不是 IP，是域名 — 检查是否指向 localhost 等
            if hostname in ('localhost', 'localhost.localdomain', 'metadata.google.internal'):
                return False
        return True
    except Exception:
        return False


class ContentEnricher:
    """内容充实器 — 为 RSS 缺少全文的文章抓取网页正文"""

    def __init__(self, enabled: bool = True, max_concurrent: int = 3, request_timeout: float = 30.0):
        self.enabled = enabled
        self.max_concurrent = max_concurrent
        self.request_timeout = request_timeout
        self._semaphore: Optional[asyncio.Semaphore] = (
            asyncio.Semaphore(max_concurrent) if enabled else None
        )
        self._session: Optional[object] = None

        if not self.enabled:
            return

        try:
            from curl_cffi.requests import AsyncSession as CurlAsyncSession
            self._curl_cls = CurlAsyncSession
        except ImportError:
            logger.warning("[Enricher] curl_cffi not installed, enrichment disabled")
            self.enabled = False
            return

        try:
            import trafilatura
            self._trafilatura = trafilatura
        except ImportError:
            logger.warning("[Enricher] trafilatura not installed, enrichment disabled")
            self.enabled = False
            return

    async def _get_session(self):
        """获取或创建 curl_cffi 会话（懒加载）"""
        if self._session is None:
            self._session = self._curl_cls(timeout=self.request_timeout)
        return self._session

    async def close(self):
        """关闭会话，释放资源"""
        if self._session:
            await self._session.close()

    def _needs_enrichment(self, article: Article) -> bool:
        """判断文章是否需要充实：内容为空或过短且与摘要相同"""
        if not article.content:
            return True
        content_text = re.sub(r'<[^>]+>', '', article.content).strip()
        if not content_text:
            return True
        if len(content_text) < 300:
            summary_text = re.sub(r'<[^>]+>', '', article.summary).strip()
            if content_text == summary_text:
                return True
        return False

    async def _rate_limit(self, url: str):
        """按域名限速，同一域名至少间隔 _DEFAULT_DELAY 秒"""
        domain = urlparse(url).netloc
        last_time = _DOMAIN_DELAY.get(domain, 0)
        wait = max(0, _DEFAULT_DELAY - (time.monotonic() - last_time))
        if wait > 0:
            await asyncio.sleep(wait)
        _DOMAIN_DELAY[domain] = time.monotonic()

    async def _fetch_html(self, url: str) -> Optional[str]:
        """使用 curl_cffi 抓取网页 HTML"""
        if not _is_safe_url(url):
            logger.warning(f"[Enricher] Blocked unsafe URL: {url}")
            return None

        # DNS Rebinding 防护：解析域名后再次验证解析到的 IP（支持 IPv4 + IPv6）
        parsed = urlparse(url)
        hostname = parsed.hostname
        if hostname:
            try:
                import socket
                infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
                for family, _, _, _, sockaddr in infos:
                    resolved = ipaddress.ip_address(sockaddr[0])
                    for network in _BLOCKED_NETWORKS:
                        if resolved in network:
                            logger.warning(f"[Enricher] DNS rebinding detected: {hostname} -> {resolved}")
                            return None
            except (socket.gaierror, ValueError):
                pass

        session = await self._get_session()
        try:
            await self._rate_limit(url)
            resp = await session.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": f"https://{parsed.netloc}/",
                },
                allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.text
            return None
        except Exception:
            return None

    def _extract_content(self, html: str, url: str) -> Optional[str]:
        """使用 trafilatura 提取正文"""
        text = self._trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        return text if text and len(text) > 100 else None

    async def enrich(self, article: Article) -> Article:
        """充实单篇文章"""
        if not self.enabled or not self._needs_enrichment(article) or not article.url:
            return article

        async with self._semaphore:
            html = await self._fetch_html(article.url)
            if html:
                full_text = await asyncio.to_thread(self._extract_content, html, article.url)
                if full_text:
                    article.content = full_text
            return article

    async def enrich_batch(self, articles: List[Article]) -> List[Article]:
        """批量充实文章"""
        if not self.enabled:
            return articles

        need_enrichment = [a for a in articles if self._needs_enrichment(a)]
        skip_count = len(articles) - len(need_enrichment)
        if skip_count > 0:
            logger.info(f"[Enricher] {skip_count} articles already have full content, skipping")

        if not need_enrichment:
            return articles

        logger.info(f"[Enricher] Enriching {len(need_enrichment)} articles (concurrency={self.max_concurrent})...")
        completed = 0

        async def _enrich_with_progress(article):
            nonlocal completed
            result = await self.enrich(article)
            completed += 1
            if completed % 50 == 0 or completed == len(need_enrichment):
                logger.info(f"[Enricher] Progress: {completed}/{len(need_enrichment)}")
            return result

        tasks = [_enrich_with_progress(a) for a in need_enrichment]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        enriched_map = {}
        for i, result in enumerate(results):
            key = id(need_enrichment[i])
            enriched_map[key] = need_enrichment[i] if isinstance(result, Exception) else result

        return [enriched_map.get(id(a), a) for a in articles]
