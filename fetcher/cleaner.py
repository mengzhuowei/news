"""内容清洗器 — SimHash 去重 + HTML 清洗 + 广告移除 + 截断 + 版权声明"""

import html as html_module
import re
import hashlib
import numpy as np
from typing import Optional, Set
from loguru import logger
from fetcher.fetchers.base import Article


class SimHash:
    """SimHash 算法 — 用于检测相似内容"""

    def __init__(self, hash_bits: int = 64):
        self.hash_bits = hash_bits

    def _get_features(self, text: str) -> list[str]:
        """3-gram 分词提取特征"""
        text = re.sub(r'[^\w\s]', '', text.lower())
        words = text.split()
        return [' '.join(words[i:i+3]) for i in range(len(words) - 2)]

    def compute(self, text: str) -> int:
        """计算文本的 SimHash 值"""
        features = self._get_features(text)
        if not features:
            return 0

        v = np.zeros(self.hash_bits, dtype=int)
        for feature in features:
            hash_val = int.from_bytes(hashlib.md5(feature.encode()).digest(), 'big') % (1 << self.hash_bits)
            for i in range(self.hash_bits):
                v[i] += 1 if (hash_val >> i) & 1 else -1

        result = 0
        for i in range(self.hash_bits):
            if v[i] > 0:
                result |= (1 << i)
        return result

    def hamming_distance(self, hash1: int, hash2: int) -> int:
        """汉明距离"""
        x = hash1 ^ hash2
        count = 0
        while x:
            count += 1
            x &= x - 1
        return count

    def is_similar(self, text1: str, text2: str, threshold: float = 0.85) -> bool:
        """判断两段文本是否相似"""
        distance = self.hamming_distance(self.compute(text1), self.compute(text2))
        similarity = 1 - (distance / self.hash_bits)
        return similarity >= threshold


class ContentCleaner:
    """AI 内容清洗器 — 去重 + HTML 清洗 + 广告移除 + 截断 + 版权声明"""

    _HASH_BITS = 64
    _BUCKET_BITS = 10
    _BUCKET_COUNT = 1 << _BUCKET_BITS
    _BUCKET_MASK = _BUCKET_COUNT - 1

    def __init__(self, duplicate_threshold: float = 0.8):
        self.duplicate_threshold = duplicate_threshold
        self.simhash = SimHash()
        self._seen_hashes: Set[int] = set()
        self._seen_titles: Set[str] = set()
        self._hash_buckets: dict[int, list[int]] = {}

    def reset(self):
        """重置去重状态 — 每轮抓取前调用"""
        self._seen_hashes.clear()
        self._seen_titles.clear()
        self._hash_buckets.clear()

    def clean(self, article: Article) -> Optional[Article]:
        """清洗文章，重复则返回 None"""
        try:
            if self._is_duplicate(article):
                return None

            article.content = self._remove_html_tags(article.content)
            article.summary = self._remove_html_tags(article.summary)
            article.content = self._remove_ads(article.content)
            article.summary = self._remove_ads(article.summary)
            article.content = self._normalize_text(article.content)
            article.summary = self._normalize_text(article.summary)

            article_hash = self.simhash.compute(article.title + article.content)
            self._add_hash(article_hash)
            self._seen_titles.add(article.title.lower())

            article.content = self._truncate_content(article.content, max_length=5000)

            return article
        except Exception as e:
            logger.error(f"[Cleaner] Error: {e}")
            return None

    def _is_duplicate(self, article: Article) -> bool:
        """三级去重：标题匹配 → SimHash 精确匹配 → SimHash 桶内模糊匹配"""
        if article.title.lower() in self._seen_titles:
            return True

        preprocessed = self._remove_html_tags(article.content)
        preprocessed = self._remove_ads(preprocessed)
        preprocessed = self._normalize_text(preprocessed)
        article_hash = self.simhash.compute(article.title + preprocessed)

        if article_hash in self._seen_hashes:
            return True

        bucket_key = article_hash & self._BUCKET_MASK
        for seen_hash in self._hash_buckets.get(bucket_key, []):
            distance = self.simhash.hamming_distance(article_hash, seen_hash)
            if 1 - (distance / self.simhash.hash_bits) >= self.duplicate_threshold:
                return True
        return False

    def _add_hash(self, article_hash: int):
        """记录 hash 到精确集合和分桶索引"""
        self._seen_hashes.add(article_hash)
        bucket_key = article_hash & self._BUCKET_MASK
        self._hash_buckets.setdefault(bucket_key, []).append(article_hash)

    def _remove_html_tags(self, text: str) -> str:
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        return html_module.unescape(text)

    def _remove_ads(self, text: str) -> str:
        patterns = [
            r'Subscribe to.*?(?:now|today)',
            r'Sign up for.*?(?:newsletter|digest)',
            r'Cookie.*?Policy',
            r'Privacy.*?Policy',
        ]
        for pattern in patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        return text

    def _normalize_text(self, text: str) -> str:
        text = re.sub(r'\s+', ' ', text)
        # 只移除控制字符，保留所有可打印 Unicode（包括智能引号、省略号等）
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        return text.strip()

    def _truncate_content(self, text: str, max_length: int = 5000) -> str:
        if len(text) <= max_length:
            return text
        truncated = text[:max_length]
        last_period = truncated.rfind('.')
        if last_period > max_length * 0.8:
            return truncated[:last_period + 1]
        return truncated + "..."

    def _add_copyright_notice(self, article: Article, content: str) -> str:
        notice = f"\n\n---\n**Source:** [{article.source}]({article.url})\n"
        if article.published:
            notice += f"*Generated at: {article.published.strftime('%Y-%m-%d %H:%M:%S')} UTC*\n"
        return content + notice
