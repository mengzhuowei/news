"""回填洗稿 — 为 is_rewritten=0 的文章补充 AI 重写"""

import asyncio
import os
import sys
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from fetcher.summarizer import Summarizer
from web.database import get_session
from web.models import Article


BATCH_SIZE = 10
CONCURRENT = 3


async def rewrite_article(summarizer: Summarizer, article_id: int, title: str, content: str) -> tuple[int, str, str, bool]:
    rewritten_content, rewritten_summary = await summarizer.rewrite(title, content)
    success = (rewritten_content != content)
    return article_id, rewritten_content, rewritten_summary, success


async def run_backfill(dry_run: bool = False):
    summarizer = Summarizer()

    if not summarizer.api_key:
        logger.error("LLM_API_KEY not set, cannot backfill")
        return

    logger.info(f"Model: {summarizer.model}, Base URL: {summarizer.base_url}")

    with get_session() as db:
        total = db.query(Article).filter(Article.is_rewritten == False).count()
    logger.info(f"Found {total} articles to backfill")

    if dry_run:
        with get_session() as db:
            articles = db.query(Article).filter(Article.is_rewritten == False).limit(5).all()
            for a in articles:
                logger.info(f"  [DRY RUN] id={a.id} title={a.title[:60]} len={len(a.content)}")
        return

    done = 0
    success_count = 0

    sem = asyncio.Semaphore(CONCURRENT)

    async def limited_rewrite(article_id: int, title: str, content: str):
        async with sem:
            return await rewrite_article(summarizer, article_id, title, content)

    for offset in range(0, total, BATCH_SIZE):
        with get_session() as db:
            batch = db.query(Article).filter(Article.is_rewritten == False).offset(offset).limit(BATCH_SIZE).all()
            if not batch:
                break
            batch_data = [(a.id, a.title, a.content) for a in batch]

        tasks = [limited_rewrite(aid, title, content) for aid, title, content in batch_data]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        with get_session() as db:
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Rewrite error: {result}")
                    done += 1
                    continue

                article_id, content, summary, success = result
                if success:
                    article = db.query(Article).filter(Article.id == article_id).first()
                    if article:
                        article.content = content
                        article.summary = summary
                        article.is_rewritten = True
                        success_count += 1
                done += 1

        pct = done / total * 100
        logger.info(f"Progress: {done}/{total} ({pct:.1f}%) — {success_count} rewritten successfully")

        await asyncio.sleep(1)

    logger.info(f"Backfill complete: {success_count}/{total} articles rewritten")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(run_backfill(dry_run=dry))
