"""清理 LLM 拒绝话术文章的 dist/ 文件
执行后只删文件，不动数据库（数据库标记 is_built=0 即可）。
"""
import os
import sys
from pathlib import Path
from datetime import datetime

# 把项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.database import get_session
from web.models import Article
from loguru import logger

# 与 site_builder 完全一致的 LLM 拒绝话术关键词
LLM_REFUSAL_PATTERNS = [
    "no original article body to rewrite",
    "no factual content, numbers, or data are available",
    "no content to rewrite",
    "no content was provided",
    "no article body",
    "the provided text contains only a title",
    "no original content to process",
]


def is_llm_refusal(content: str) -> bool:
    """检测是否为 LLM 拒绝话术"""
    if not content:
        return True
    content_lower = content.lower()
    return any(p in content_lower for p in LLM_REFUSAL_PATTERNS)


def find_affected_urls() -> list[tuple[int, str]]:
    """查找受影响的文章 ID 和 permalink 形式
    不再过滤 is_built——如果文章正文是 LLM 拒绝话术，无论是否构建过都该清理
    """
    from urllib.parse import urlparse
    affected = []
    with get_session() as db:
        # 不加 is_built 过滤，因为 dist/ 里可能存在
        # 但前端构建时已跳过的脏数据
        articles = db.query(Article).all()
        for a in articles:
            if is_llm_refusal(a.content or ""):
                # 构造文章 URL 路径：/category/date/slug/
                if not a.slug or not a.category or not a.published_at:
                    continue
                date_path = a.published_at.strftime("%Y/%m/%d")
                affected.append((a.id, a.category, date_path, a.slug, a.title))
    return affected


def cleanup_dist_files(dry_run: bool = True) -> dict:
    """删除受影响文章的 dist/ 文件
    dry_run=True: 只打印不删
    dry_run=False: 实际删除
    """
    affected = find_affected_urls()
    if not affected:
        logger.info("No LLM-refusal articles found in built articles.")
        return {"found": 0, "deleted": 0, "missing": 0}

    logger.info(f"Found {len(affected)} LLM-refusal articles in DB.")

    # dist/ 路径
    dist_dir = Path(__file__).parent.parent / "dist"
    if not dist_dir.exists():
        logger.error(f"dist/ not found: {dist_dir}")
        return {"found": len(affected), "deleted": 0, "missing": 0}

    deleted = 0
    missing = 0
    for aid, category, date_path, slug, title in affected:
        article_dir = dist_dir / category / date_path / slug
        index_file = article_dir / "index.html"
        if not index_file.exists():
            missing += 1
            continue

        if dry_run:
            logger.info(f"[DRY-RUN] would delete: {index_file.relative_to(dist_dir)}")
        else:
            index_file.unlink()
            # 删空目录
            try:
                article_dir.rmdir()
            except OSError:
                pass  # 目录不空就不删
            deleted += 1
            logger.info(f"Deleted: {index_file.relative_to(dist_dir)}")

    return {"found": len(affected), "deleted": deleted, "missing": missing}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Clean up LLM-refusal article HTML files")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Only print what would be deleted (default)")
    parser.add_argument("--execute", action="store_true",
                        help="Actually delete files (overrides --dry-run)")
    args = parser.parse_args()

    dry_run = not args.execute
    if not dry_run:
        confirm = input("This will DELETE files. Type 'yes' to confirm: ")
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

    result = cleanup_dist_files(dry_run=dry_run)
    print(f"\nResult: {result}")
