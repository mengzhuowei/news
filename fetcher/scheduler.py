"""定时抓取调度器 — 协调全流程"""

import asyncio
import os
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

    async def fetch_and_process(self):
        """完整抓取流程：抓取 → 充实 → 去重 → 清洗 → 洗稿 → 入库 → 清理 → 构建"""
        logger.info(f"[{datetime.now(timezone.utc)}] Starting scheduled fetch...")

        # 每轮重置去重状态，防止跨周期内存无限增长
        self.cleaner._seen_hashes.clear()
        self.cleaner._seen_titles.clear()

        try:
            # 1. 并发抓取（逐源记录 FetchLog）
            tasks = [self._fetch_source(f) for f in self.aggregator.fetchers]
            results = await asyncio.gather(*tasks)
            all_articles = [a for batch in results for a in batch]

            # URL 精确去重
            seen_urls = set()
            articles = []
            for article in all_articles:
                if article.url not in seen_urls:
                    seen_urls.add(article.url)
                    articles.append(article)

            if not articles:
                return

            # 2. 分类归一化
            for article in articles:
                article.category = _CATEGORY_MAP.get(article.category, article.category)

            # 3. 充实
            if self.enricher.enabled:
                articles = await self.enricher.enrich_batch(articles)

            # 4. 逐篇处理
            saved_count = 0
            for article in articles:
                if await asyncio.to_thread(self._is_in_db, article.url):
                    continue

                cleaned = self.cleaner.clean(article)
                if not cleaned:
                    continue

                # AI 洗稿
                if self.summarizer.api_key:
                    rewritten_content, rewritten_summary = await self.summarizer.rewrite(
                        cleaned.title, cleaned.content,
                    )
                    cleaned.content = rewritten_content
                    cleaned.summary = rewritten_summary
                    cleaned.rewritten = True
                else:
                    cleaned.rewritten = False

                # 统一添加版权声明（AI 重写前不附加，避免重复）
                cleaned.content = self.cleaner._add_copyright_notice(cleaned, cleaned.content)

                await asyncio.to_thread(self._save_to_db, cleaned)
                saved_count += 1

            # 5. 清理过期
            await asyncio.to_thread(self._cleanup_expired_articles, 7)

            # 6. 构建站点
            if saved_count > 0:
                await asyncio.to_thread(self._build_static_site)

            logger.info(f"Fetch complete: {saved_count} saved")

        except Exception as e:
            logger.error(f"Fetch error: {e}")

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
            # 使用分配的 ID 生成唯一 slug
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
