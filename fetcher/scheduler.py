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
    "technology": "technology",
    "artificial-intelligence": "technology",
    "machine-learning": "technology",
    "nlp": "technology",
    "computer-vision": "technology",
    "research": "technology",
    "business": "business",
    "markets": "business",
    "finance": "business",
    "economics": "business",
    "world": "world",
    "politics": "world",
    "news": "world",
    "science": "science",
}

BUILD_BATCH_SIZE = int(os.getenv("BUILD_BATCH_SIZE", "20"))
BUILD_COOLDOWN_SECONDS = int(os.getenv("BUILD_COOLDOWN_SECONDS", "60"))


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
            max_concurrent=int(os.getenv("ENRICHMENT_CONCURRENT", "3")),
        )
        self._build_lock = threading.Lock()
        self._last_build_at: Optional[datetime] = None

    async def fetch_and_process(self):
        """完整抓取流程：抓取 → 充实 → 去重 → 清洗 → 洗稿 → 入库(增量构建) → 清理 → 最终构建"""
        logger.info(f"[{datetime.now(timezone.utc)}] Starting scheduled fetch...")

        self.cleaner._seen_hashes.clear()
        self.cleaner._seen_titles.clear()

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
                return

            for article in articles:
                article.category = _CATEGORY_MAP.get(article.category, article.category)

            if self.enricher.enabled:
                articles = await self.enricher.enrich_batch(articles)

            saved_count = 0
            for article in articles:
                if await asyncio.to_thread(self._is_in_db, article.url):
                    continue

                cleaned = self.cleaner.clean(article)
                if not cleaned:
                    continue

                if self.summarizer.api_key:
                    rewritten_content, rewritten_summary = await self.summarizer.rewrite(
                        cleaned.title, cleaned.content,
                    )
                    cleaned.content = rewritten_content
                    cleaned.summary = rewritten_summary
                    cleaned.rewritten = True
                else:
                    cleaned.rewritten = False

                cleaned.content = self.cleaner._add_copyright_notice(cleaned, cleaned.content)

                await asyncio.to_thread(self._save_to_db, cleaned)
                saved_count += 1

                if saved_count % BUILD_BATCH_SIZE == 0:
                    self._try_async_build(f"batch ({saved_count} saved)")

            await asyncio.to_thread(self._cleanup_expired_articles, 7)

            if saved_count > 0:
                await asyncio.to_thread(self._build_static_site)

            logger.info(f"Fetch complete: {saved_count} saved")

        except Exception as e:
            logger.error(f"Fetch error: {e}")

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

        try:
            from fetcher.site_builder import SiteBuilder
            project_root = Path(__file__).parent.parent
            output_dir = Path(os.getenv("DIST_DIR", str(project_root / "dist")))
            builder = SiteBuilder(
                content_dir=project_root / "shared-content",
                output_dir=output_dir,
                use_db=True,
            )
            success = builder.build_all()
            if success:
                logger.info(f"Async build succeeded ({reason})")
            else:
                logger.error(f"Async build failed ({reason})")
        except Exception as e:
            logger.error(f"Async build error ({reason}): {e}")

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
        from web.database import SessionLocal
        from web.models import FetchLog

        db = SessionLocal()
        try:
            log = FetchLog(
                source=source,
                articles_count=articles_count,
                status=status,
                error_message=error_message,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            db.add(log)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"FetchLog save error: {e}")
        finally:
            db.close()

    def _save_to_db(self, article):
        """保存文章到数据库"""
        from web.database import SessionLocal
        from web.models import Article as ArticleModel

        db = SessionLocal()
        try:
            if db.query(ArticleModel).filter(ArticleModel.url == article.url).first():
                return
            record = ArticleModel(
                url=article.url,
                title=article.title,
                summary=article.summary or '',
                content=article.content or '',
                source=article.source,
                category=article.category,
                author=(article.author or 'Unknown')[:10000],
                image_url=article.image_url or '',
                is_rewritten=article.rewritten,
                published_at=article.published,
            )
            db.add(record)
            db.commit()
            record.slug = make_slug(article.source, article.title, record.id)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"DB save error: {e}")
        finally:
            db.close()

    def _is_in_db(self, url: str) -> bool:
        """检查文章是否已在数据库中"""
        from web.database import SessionLocal
        from web.models import Article as ArticleModel

        db = SessionLocal()
        try:
            return db.query(ArticleModel).filter(ArticleModel.url == url).first() is not None
        finally:
            db.close()

    def _cleanup_expired_articles(self, days: int = 7):
        """清理过期文章（分批处理，同时删除关联的 Markdown 文件）"""
        from web.database import SessionLocal
        from web.models import Article as ArticleModel

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        content_dir = Path(os.getenv("CONTENT_DIR", str(Path(__file__).parent.parent / "shared-content")))
        batch_size = 500
        db = SessionLocal()
        try:
            total_removed = 0
            while True:
                articles = db.query(ArticleModel).filter(ArticleModel.published_at < cutoff).limit(batch_size).all()
                if not articles:
                    break
                file_info = [(a.slug, a.category) for a in articles]
                for article in articles:
                    db.delete(article)
                db.commit()
                for slug, category in file_info:
                    if slug:
                        md_path = content_dir / (category or "uncategorized") / f"{slug}.md"
                        if md_path.exists():
                            md_path.unlink()
                total_removed += len(articles)
            logger.info(f"Cleaned up {total_removed} expired articles")
        except Exception as e:
            db.rollback()
            logger.error(f"Cleanup error: {e}")
        finally:
            db.close()

    def _build_static_site(self):
        """构建静态站点"""
        from fetcher.site_builder import SiteBuilder

        project_root = Path(__file__).parent.parent
        output_dir = Path(os.getenv("DIST_DIR", str(project_root / "dist")))
        builder = SiteBuilder(
            content_dir=project_root / "shared-content",
            output_dir=output_dir,
            use_db=True,
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
