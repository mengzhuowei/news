"""清理低质量文章：删除 dist/ HTML 文件 + 标记数据库 is_built=0

检测规则（与 site_builder 保持一致）：
  1. wordCount < 50（极短内容）
  2. LLM 拒绝话术（"no article body to rewrite" 等）
  3. Source-only 空内容（只有 "---\\n**Source:**" 标注行）
  4. 空内容

两步操作：
  Step 1: 标记数据库 is_built=0（防止 site_builder 重新生成）
  Step 2: 删除 dist/ 中的 HTML 文件

用法：
  python -m scripts.cleanup_low_quality --dry-run     # 预览
  python -m scripts.cleanup_low_quality --execute      # 执行
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.database import get_session
from web.models import Article
from loguru import logger

# --- 检测规则 ---

MIN_WORDCOUNT = 50

LLM_REFUSAL_PATTERNS = [
    "no original article body to rewrite",
    "no factual content, numbers, or data are available",
    "no content to rewrite",
    "no content was provided",
    "no article body",
    "the provided text contains only a title",
    "no original content to process",
]


def classify(content: str) -> str | None:
    """返回低质量原因，或 None（高质量）"""
    if not content or not content.strip():
        return "empty"
    content_stripped = content.strip()

    # LLM 拒绝话术
    content_lower = content_stripped.lower()
    for p in LLM_REFUSAL_PATTERNS:
        if p in content_lower:
            return "llm_refusal"

    # Source-only 空内容
    if content_stripped.startswith("---") and "**Source:**" in content_stripped:
        return "source_only"

    # wordCount < 50
    word_count = len(content_stripped.split())
    if word_count < MIN_WORDCOUNT:
        return f"short_{word_count}words"

    return None


def find_low_quality_articles() -> list[tuple[int, str, str, str, str, str]]:
    """查找所有低质量文章
    返回: [(id, category, date_path, slug, title, reason), ...]
    """
    affected = []
    stats = {}
    with get_session() as db:
        articles = db.query(Article).all()
        total = 0
        for a in articles:
            reason = classify(a.content or "")
            if reason is None:
                continue
            total += 1
            stats[reason] = stats.get(reason, 0) + 1
            if not a.slug or not a.category or not a.published_at:
                continue
            date_path = a.published_at.strftime("%Y/%m/%d")
            affected.append((a.id, a.category, date_path, a.slug, a.title, reason))

    logger.info(f"Low-quality articles in DB: {total}")
    for reason, cnt in sorted(stats.items(), key=lambda x: -x[1]):
        logger.info(f"  {reason}: {cnt}")
    return affected


def mark_db_is_built_zero(article_ids: list[int]) -> int:
    """标记数据库 is_built=0，防止 site_builder 重新生成"""
    if not article_ids:
        return 0
    with get_session() as db:
        updated = db.query(Article).filter(
            Article.id.in_(article_ids)
        ).update({"is_built": False}, synchronize_session=False)
        db.commit()
    return updated


def cleanup_dist_files(affected: list[tuple], dry_run: bool = True) -> dict:
    """删除低质量文章的 dist/ 文件"""
    if not affected:
        logger.info("No low-quality articles found.")
        return {"found": 0, "deleted": 0, "missing": 0}

    dist_dir = Path(__file__).parent.parent / "dist"
    if not dist_dir.exists():
        logger.error(f"dist/ not found: {dist_dir}")
        return {"found": len(affected), "deleted": 0, "missing": 0}

    deleted = 0
    missing = 0
    for aid, category, date_path, slug, title, reason in affected:
        article_dir = dist_dir / category / date_path / slug
        index_file = article_dir / "index.html"
        if not index_file.exists():
            missing += 1
            continue

        rel = index_file.relative_to(dist_dir)
        if dry_run:
            logger.info(f"[DRY-RUN] [{reason}] {rel}")
        else:
            index_file.unlink()
            # 尝试删空目录（日/月/年逐级向上）
            for parent in [article_dir, article_dir.parent, article_dir.parent.parent]:
                try:
                    parent.rmdir()
                except OSError:
                    break
            deleted += 1
            logger.info(f"[DELETED] [{reason}] {rel}")

    return {"found": len(affected), "deleted": deleted, "missing": missing}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Clean up low-quality articles: mark DB + delete dist/ files")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Only print what would be deleted (default)")
    parser.add_argument("--execute", action="store_true",
                        help="Actually mark DB + delete files (overrides --dry-run)")
    args = parser.parse_args()

    dry_run = not args.execute

    # Step 0: 扫描
    affected = find_low_quality_articles()
    if not affected:
        print("No low-quality articles found. Done.")
        sys.exit(0)

    article_ids = [a[0] for a in affected]

    if dry_run:
        # dry-run: 只预览要删的文件，不改数据库
        print(f"\n[DRY-RUN] Found {len(affected)} low-quality articles.")
        print(f"[DRY-RUN] Would mark is_built=0 for {len(article_ids)} articles in DB.")
        print(f"[DRY-RUN] Would delete dist/ files:\n")
        cleanup_dist_files(affected, dry_run=True)
        print(f"\n[DRY-RUN] No changes made. Use --execute to apply.")
    else:
        confirm = input(
            f"This will:\n"
            f"  1. Mark is_built=0 for {len(article_ids)} articles in DB\n"
            f"  2. Delete their dist/ HTML files\n"
            f"Type 'yes' to confirm: "
        )
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

        # Step 1: 标记数据库
        logger.info("Step 1: Marking is_built=0 in database...")
        updated = mark_db_is_built_zero(article_ids)
        logger.info(f"Marked {updated} articles as is_built=0.")

        # Step 2: 删除 dist/ 文件
        logger.info("Step 2: Deleting dist/ HTML files...")
        result = cleanup_dist_files(affected, dry_run=False)
        print(f"\nDone! DB updated: {updated}, Files deleted: {result['deleted']}, "
              f"Files missing (already gone): {result['missing']}")
