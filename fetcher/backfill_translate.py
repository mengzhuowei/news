"""回填翻译 — 为 is_translated=0 的文章补充多语言翻译"""

import asyncio
import os
import sys
from loguru import logger

from fetcher.translator import Translator
from web.database import get_session
from web.models import Article


BATCH_SIZE = 10
CONCURRENT = 2


async def translate_article(
    translator: Translator, article_id: int, title: str, content: str, summary: str,
) -> tuple[int, dict, bool]:
    """翻译单篇文章，返回 (article_id, translations, success)"""
    translations = await translator.translate_article(title, content, summary)
    success = bool(translations)
    return article_id, translations, success


async def run_backfill(dry_run: bool = False):
    translator = Translator(concurrent=CONCURRENT)

    if not translator.api_key:
        logger.error("LLM_API_KEY not set, cannot translate")
        return

    logger.info(f"Model: {translator.model}, Base URL: {translator.base_url}")
    logger.info(f"Target languages: {translator.languages}")

    with get_session() as db:
        total = db.query(Article).filter(Article.is_translated == False).count()
    logger.info(f"Found {total} articles to translate")

    if dry_run:
        with get_session() as db:
            articles = db.query(Article).filter(Article.is_translated == False).limit(5).all()
            for a in articles:
                logger.info(f"  [DRY RUN] id={a.id} title={a.title[:60]} len={len(a.content)}")
        return

    done = 0
    success_count = 0

    sem = asyncio.Semaphore(CONCURRENT)

    async def limited_translate(
        article_id: int, title: str, content: str, summary: str,
    ):
        async with sem:
            return await translate_article(translator, article_id, title, content, summary)

    while True:
        with get_session() as db:
            batch = db.query(Article).filter(Article.is_translated == False).limit(BATCH_SIZE).all()
            if not batch:
                break
            batch_data = [(a.id, a.title, a.content, a.summary or "") for a in batch]

        tasks = [limited_translate(aid, title, content, summary) for aid, title, content, summary in batch_data]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        with get_session() as db:
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Translate error: {result}")
                    done += 1
                    continue

                article_id, translations, success = result
                if success:
                    article = db.query(Article).filter(Article.id == article_id).first()
                    if article:
                        article.translations = translations
                        article.is_translated = True
                        success_count += 1
                done += 1

        pct = done / total * 100
        logger.info(f"Progress: {done}/{total} ({pct:.1f}%) — {success_count} translated successfully")

        await asyncio.sleep(1)

    logger.info(f"Backfill translate complete: {success_count}/{total} articles translated")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(run_backfill(dry_run=dry))
