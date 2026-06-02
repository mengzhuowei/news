"""定时抓取调度器 — 协调全流程"""

import asyncio
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from loguru import logger

from fetcher.aggregator import Aggregator
from fetcher.cleaner import ContentCleaner
from fetcher.summarizer import Summarizer
from fetcher.content_enricher import ContentEnricher

_CATEGORY_MAP = {
    # 科技
    "technology": "technology",
    "artificial-intelligence": "technology",
    "machine-learning": "technology",
    "nlp": "technology",
    "computer-vision": "technology",
    "research": "technology",

    # 商业 / 金融
    "business": "business",
    "companies": "business",
    "markets": "markets",
    "finance": "finance",
    "wealth": "finance",
    "economics": "economics",

    # 世界 / 地区
    "world": "world",
    "uk": "world",
    "us": "world",
    "united-states": "world",
    "news": "world",

    # 政治
    "politics": "politics",

    # 科学
    "science": "science",
    "climate": "science",

    # 独立分类
    "top": "breaking-news",
    "health": "health",
    "culture": "culture",
    "education": "education",
    "sport": "sport",
}

BUILD_BATCH_SIZE = int(os.getenv("BUILD_BATCH_SIZE", "20"))
BUILD_COOLDOWN_SECONDS = int(os.getenv("BUILD_COOLDOWN_SECONDS", "60"))
REWRITE_CONCURRENT = int(os.getenv("REWRITE_CONCURRENT", "3"))


def make_slug(source: str, title: str, article_id: int = 0) -> str:
    """生成 URL 友好的 slug — 包含 article_id 保证唯一性"""
    safe_source = "".join(c for c in source.lower() if c.isalnum() or c in ('-', '_'))
    safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_'))
    safe_title = safe_title.strip().replace(' ', '-')
    return f"{safe_source}_{safe_title[:50]}_{article_id}"


class FetchScheduler:
    """定时抓取调度器 — 协调全流程"""

    def __init__(self, interval_minutes: int = 15):
        self.interval_minutes = interval_minutes
        self.aggregator = Aggregator()
        self.cleaner = ContentCleaner()
        self.summarizer = Summarizer()
        self.enricher = ContentEnricher(
            enabled=os.getenv("CONTENT_ENRICHMENT", "true").lower() == "true",
            max_concurrent=int(os.getenv("ENRICHMENT_CONCURRENT", "5")),
        )
        self._build_lock = threading.Lock()
        self._build_run_lock = threading.Lock()
        self._last_build_at: Optional[datetime] = None

    async def fetch_and_process(self):
        """完整抓取流程：抓取 → 去重URL → 过滤已入库 → 充实 → 清洗 → 洗稿 → 入库 → 清理 → 构建"""
        logger.info(f"[{datetime.now(timezone.utc)}] Starting scheduled fetch...")

        self.cleaner.reset()

        try:
            tasks = [self._fetch_source(f) for f in self.aggregator.fetchers]
            results = await asyncio.gather(*tasks)
            all_articles = [a for batch in results for a in batch]

            seen_urls = set()
            articles = []
            for article in all_articles:
                if article.url not in seen_urls:
                    seen_urls.add(article.url)
                    articles.append(article)

            if not articles:
                logger.info("No articles fetched, skipping")
                return

            for article in articles:
                article.category = _CATEGORY_MAP.get(article.category, article.category)

            existing_urls = await asyncio.to_thread(self._batch_check_db, articles)
            new_articles = [a for a in articles if a.url not in existing_urls]
            logger.info(f"Fetched {len(articles)} articles, {len(new_articles)} new, {len(existing_urls)} already in DB")

            if not new_articles:
                logger.info("No new articles to process")
                return

            if self.enricher.enabled:
                logger.info(f"Enriching {len(new_articles)} articles...")
                new_articles = await self.enricher.enrich_batch(new_articles)

            saved_count = 0
            if self.summarizer.api_key:
                logger.info(f"Rewriting {len(new_articles)} articles (concurrency={REWRITE_CONCURRENT})...")
                saved_count = await self._process_with_rewrite(new_articles)
            else:
                for article in new_articles:
                    cleaned = self.cleaner.clean(article)
                    if not cleaned:
                        continue
                    cleaned.content = self.cleaner._add_copyright_notice(cleaned, cleaned.content)
                    cleaned.rewritten = False
                    if await asyncio.to_thread(self._save_to_db, cleaned):
                        saved_count += 1
                    if saved_count > 0 and saved_count % BUILD_BATCH_SIZE == 0:
                        self._try_async_build(f"batch ({saved_count} saved)")

            await asyncio.to_thread(self._cleanup_expired_articles, 7)

            if saved_count > 0:
                await asyncio.to_thread(self._build_static_site)

            logger.info(f"Fetch complete: {saved_count} saved")

        except Exception as e:
            logger.error(f"Fetch error: {e}")

    async def _process_with_rewrite(self, articles: list) -> int:
        """并发洗稿 + 入库流水线"""
        semaphore = asyncio.Semaphore(REWRITE_CONCURRENT)
        saved_count = 0
        count_lock = asyncio.Lock()

        async def _process_one(article):
            nonlocal saved_count
            cleaned = self.cleaner.clean(article)
            if not cleaned:
                return

            async with semaphore:
                rewritten_content, rewritten_summary = await self.summarizer.rewrite(
                    cleaned.title, cleaned.content,
                )

            cleaned.content = rewritten_content
            cleaned.summary = rewritten_summary
            cleaned.rewritten = True
            cleaned.content = self.cleaner._add_copyright_notice(cleaned, cleaned.content)

            saved = await asyncio.to_thread(self._save_to_db, cleaned)
            if not saved:
                return

            async with count_lock:
                saved_count += 1
                current_count = saved_count

            if current_count % BUILD_BATCH_SIZE == 0:
                self._try_async_build(f"batch ({current_count} saved)")
                logger.info(f"Progress: {current_count} articles saved")

        tasks = [_process_one(a) for a in articles]
        await asyncio.gather(*tasks, return_exceptions=True)
        return saved_count

    def _batch_check_db(self, articles: list) -> set:
        """批量查询已存在的 URL，一次 DB 查询替代 N 次单条查询"""
        from web.database import get_session
        from web.models import Article as ArticleModel

        urls = [a.url for a in articles]
        existing = set()
        batch_size = 500
        with get_session() as db:
            for i in range(0, len(urls), batch_size):
                chunk = urls[i:i + batch_size]
                rows = db.query(ArticleModel.url).filter(ArticleModel.url.in_(chunk)).all()
                existing.update(r[0] for r in rows)
        return existing

    def _try_async_build(self, reason: str = ""):
        """尝试在后台线程异步构建站点（带节流）"""
        with self._build_lock:
            now = datetime.now(timezone.utc)
            if self._last_build_at and (now - self._last_build_at).total_seconds() < BUILD_COOLDOWN_SECONDS:
                logger.debug(f"Build skipped (cooldown): {reason}")
                return

        thread = threading.Thread(target=self._async_build_worker, args=(reason,), daemon=True)
        thread.start()

    def _async_build_worker(self, reason: str):
        """后台构建线程"""
        with self._build_lock:
            now = datetime.now(timezone.utc)
            if self._last_build_at and (now - self._last_build_at).total_seconds() < BUILD_COOLDOWN_SECONDS:
                return
            self._last_build_at = now

        if not self._build_run_lock.acquire(blocking=False):
            logger.debug(f"Build skipped (another build in progress): {reason}")
            return

        try:
            from fetcher.site_builder import SiteBuilder
            project_root = Path(__file__).parent.parent
            output_dir = Path(os.getenv("DIST_DIR", str(project_root / "dist")))
            builder = SiteBuilder(
                output_dir=output_dir,
            )
            success = builder.build_all()
            if success:
                logger.info(f"Async build succeeded ({reason})")
            else:
                logger.error(f"Async build failed ({reason})")
        except Exception as e:
            logger.error(f"Async build error ({reason}): {e}")
        finally:
            self._build_run_lock.release()

    async def shutdown(self):
        """清理所有资源（进程退出时调用）"""
        await self.aggregator.close_all()
        await self.enricher.close()

    async def _fetch_source(self, fetcher) -> list:
        """抓取单个源并记录 FetchLog"""
        started_at = datetime.now(timezone.utc)
        try:
            articles = await fetcher.fetch()
            await asyncio.to_thread(
                self._save_fetch_log,
                source=fetcher.source_name,
                articles_count=len(articles),
                status='success',
                started_at=started_at,
            )
            return articles
        except Exception as e:
            await asyncio.to_thread(
                self._save_fetch_log,
                source=fetcher.source_name,
                articles_count=0,
                status='failed',
                error_message=str(e),
                started_at=started_at,
            )
            logger.error(f"[{fetcher.source_name}] Fetch failed: {e}")
            return []

    def _save_fetch_log(
        self, source: str, articles_count: int, status: str,
        started_at: datetime, error_message: Optional[str] = None,
    ):
        """记录抓取日志到数据库"""
        from web.database import get_session
        from web.models import FetchLog

        try:
            with get_session() as db:
                log = FetchLog(
                    source=source,
                    articles_count=articles_count,
                    status=status,
                    error_message=error_message,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )
                db.add(log)
        except Exception as e:
            logger.error(f"FetchLog save error: {e}")

    def _save_to_db(self, article) -> bool:
        """保存文章到数据库，返回是否成功 — 使用 IntegrityError 处理并发插入"""
        from sqlalchemy.exc import IntegrityError
        from web.database import get_session
        from web.models import Article as ArticleModel

        try:
            with get_session() as db:
                record = ArticleModel(
                    url=article.url,
                    title=article.title,
                    summary=article.summary or '',
                    content=article.content or '',
                    source=article.source,
                    category=article.category,
                    author=(article.author or 'Unknown')[:255],
                    image_url=article.image_url or '',
                    is_rewritten=article.rewritten,
                    published_at=article.published,
                    slug=make_slug(article.source, article.title, 0),
                )
                db.add(record)
                db.flush()
                record.slug = make_slug(article.source, article.title, record.id)
            return True
        except IntegrityError:
            logger.debug(f"Article already exists (concurrent insert): {article.url[:80]}")
            return False
        except Exception as e:
            logger.error(f"DB save error: {e}")
            return False

    def _cleanup_expired_articles(self, days: int = 7):
        """清理过期文章 — 每批使用独立 session，避免长事务 rollback"""
        from web.database import get_session
        from web.models import Article as ArticleModel

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        batch_size = 500
        total_removed = 0
        while True:
            try:
                with get_session() as db:
                    articles = db.query(ArticleModel).filter(ArticleModel.published_at < cutoff).limit(batch_size).all()
                    if not articles:
                        break
                    for article in articles:
                        db.delete(article)
                    total_removed += len(articles)
            except Exception as e:
                logger.error(f"Cleanup batch error: {e}")
                break
        if total_removed > 0:
            logger.info(f"Cleaned up {total_removed} expired articles")

    def _build_static_site(self):
        """构建静态站点（等待正在进行的构建完成后再执行）"""
        from fetcher.site_builder import SiteBuilder

        with self._build_run_lock:
            project_root = Path(__file__).parent.parent
            output_dir = Path(os.getenv("DIST_DIR", str(project_root / "dist")))
            builder = SiteBuilder(
                output_dir=output_dir,
            )
            success = builder.build_all()
            with self._build_lock:
                self._last_build_at = datetime.now(timezone.utc)
            if success:
                logger.info("Static site built successfully")
            else:
                logger.error("Failed to build static site")

    async def run_continuously(self):
        """持续运行调度器"""
        logger.info(f"Scheduler started, interval={self.interval_minutes}min")
        while True:
            await self.fetch_and_process()
            await asyncio.sleep(self.interval_minutes * 60)


async def main():
    import sys
    from web.database import init_db

    init_db()

    interval = int(os.getenv("UPDATE_INTERVAL", "15"))
    scheduler = FetchScheduler(interval_minutes=interval)

    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--once":
            await scheduler.fetch_and_process()
        else:
            await scheduler.run_continuously()
    finally:
        await scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
