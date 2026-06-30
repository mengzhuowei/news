"""
一次性脚本：清理数据库中 articles.content 字段里旧的版权标注
模式: \n\n---\n**Source:** [xxx](url)\n*Generated at: xxx UTC*\n
"""
import re
import sys
from pathlib import Path

# 加载环境变量
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from web.database import get_session
from web.models import Article

# 匹配末尾的 ---\n**Source:** [...]...\n*Generated at: ...UTC*\n
# 兼容有没有 Generated at 行的情况
PATTERN = re.compile(
    r'\n*---\n\*\*Source:\*\*.*?(?:\n\*Generated at:.*?UTC\*)?\s*$',
    re.DOTALL,
)


def clean_content(content: str) -> tuple[str, bool]:
    if not content:
        return content, False
    new_content = PATTERN.sub('', content).rstrip()
    # 保留原文末尾换行
    if new_content and new_content != content:
        return new_content + '\n', True
    return content, False


def main():
    with get_session() as db:
        # 分批查，避免内存炸
        batch_size = 500
        offset = 0
        total_cleaned = 0
        total_checked = 0

        while True:
            articles = (
                db.query(Article)
                .filter(Article.content.like('%**Source:**%'))
                .offset(offset)
                .limit(batch_size)
                .all()
            )
            if not articles:
                break

            for a in articles:
                total_checked += 1
                new_content, changed = clean_content(a.content)
                if changed:
                    a.content = new_content
                    total_cleaned += 1

            db.commit()
            db.expunge_all()
            offset += batch_size

        print(f"检查 {total_checked} 篇文章，清洗 {total_cleaned} 篇")
        return total_cleaned


if __name__ == "__main__":
    count = main()
    sys.exit(0 if count >= 0 else 1)
