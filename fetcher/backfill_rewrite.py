"""回填洗稿 — 为 is_rewritten=0 的文章补充 AI 重写"""

import asyncio
import os
import sys
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from fetcher.summarizer import Summarizer
from web.database import SessionLocal
from web.models import Article


BATCH_SIZE = 10
CONCURRENT = 3


async def rewrite_article(summarizer: Summarizer, article: Article) -> tuple[int, str, str, bool]:
    rewritten_content, rewritten_summary = await summarizer.rewrite(article.title, article.content)
    success = (rewritten_content != article.content)
    return article.id, rewritten_content, rewritten_summary, success


async def run_backfill(dry_run: bool = False):
    summarizer = Summarizer()

    if not summarizer.api_key:
        logger.error("LLM_API_KEY not set, cannot backfill")
        return

    logger.info(f"Model: {summarizer.model}, Base URL: {summarizer.base_url}")

    db = SessionLocal()
    try:
        articles = db.query(Article).filter(Article.is_rewritten == False).all()
        logger.info(f"Found {len(articles)} articles to backfill")

        if dry_run:
            for a in articles[:5]:
                logger.info(f"  [DRY RUN] id={a.id} title={a.title[:60]} len={len(a.content)}")
            return

        total = len(articles)
        done = 0
        success_count = 0

        sem = asyncio.Semaphore(CONCURRENT)

        async def limited_rewrite(article):
            async with sem:
                return await rewrite_article(summarizer, article)

        for i in range(0, total, BATCH_SIZE):
            batch = articles[i:i + BATCH_SIZE]
            tasks = [limited_rewrite(a) for a in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Rewrite error: {result}")
                    done += 1
                    continue

                article_id, content, summary, success = result
                if success:
                    article = db.query(Article).get(article_id)
                    if article:
                        article.content = content
                        article.summary = summary
                        article.is_rewritten = True
                        success_count += 1
                done += 1

            db.commit()

            pct = done / total * 100
            logger.info(f"Progress: {done}/{total} ({pct:.1f}%) — {success_count} rewritten successfully")

            await asyncio.sleep(1)

        logger.info(f"Backfill complete: {success_count}/{total} articles rewritten")
    finally:
        db.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(run_backfill(dry_run=dry))
