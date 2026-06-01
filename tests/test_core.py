"""核心模块单元测试 — SimHash / ContentCleaner / Summarizer / _is_safe_url"""

import hashlib
import re
import pytest

from fetcher.cleaner import SimHash, ContentCleaner
from fetcher.summarizer import Summarizer
from fetcher.content_enricher import _is_safe_url
from fetcher.fetchers.base import Article
from datetime import datetime, timezone


class TestSimHash:
    def test_identical_text_same_hash(self):
        sh = SimHash()
        text = "The quick brown fox jumps over the lazy dog"
        assert sh.compute(text) == sh.compute(text)

    def test_similar_text_detected(self):
        sh = SimHash()
        text1 = "The quick brown fox jumps over the lazy dog"
        text2 = "The quick brown fox jumps over the lazy cat"
        assert sh.is_similar(text1, text2, threshold=0.8)

    def test_different_text_not_similar(self):
        sh = SimHash()
        text1 = "The quick brown fox jumps over the lazy dog"
        text2 = "Quantum computing leverages superposition and entanglement"
        assert not sh.is_similar(text1, text2, threshold=0.8)

    def test_empty_text_returns_zero(self):
        sh = SimHash()
        assert sh.compute("") == 0

    def test_hamming_distance_symmetric(self):
        sh = SimHash()
        h1 = sh.compute("hello world")
        h2 = sh.compute("goodbye world")
        assert sh.hamming_distance(h1, h2) == sh.hamming_distance(h2, h1)

    def test_performance_digest_vs_hexdigest(self):
        sh = SimHash()
        feature = "test feature for hash"
        h_digest = int.from_bytes(hashlib.md5(feature.encode()).digest(), 'big') % (1 << sh.hash_bits)
        h_hex = int(hashlib.md5(feature.encode()).hexdigest(), 16) % (2 ** sh.hash_bits)
        assert h_digest == h_hex


class TestContentCleaner:
    def _make_article(self, title="Test", content="Test content", url="https://example.com/test"):
        return Article(
            title=title,
            url=url,
            content=content,
            summary="Summary",
            published=datetime.now(timezone.utc),
            source="TestSource",
            category="technology",
        )

    def test_clean_returns_article(self):
        cleaner = ContentCleaner()
        article = self._make_article()
        result = cleaner.clean(article)
        assert result is not None
        assert result.title == "Test"

    def test_duplicate_title_detected(self):
        cleaner = ContentCleaner()
        a1 = self._make_article(title="Same Title", content="Content A")
        a2 = self._make_article(title="Same Title", content="Content B", url="https://example.com/test2")
        assert cleaner.clean(a1) is not None
        assert cleaner.clean(a2) is None

    def test_html_tags_removed(self):
        cleaner = ContentCleaner()
        article = self._make_article(content="<p>Hello <b>world</b></p>")
        result = cleaner.clean(article)
        assert result is not None
        assert "<p>" not in result.content
        assert "<b>" not in result.content

    def test_truncation(self):
        cleaner = ContentCleaner()
        long_content = "A" * 10000
        article = self._make_article(content=long_content)
        result = cleaner.clean(article)
        assert result is not None
        assert len(result.content) <= 5003

    def test_reset_clears_state(self):
        cleaner = ContentCleaner()
        a1 = self._make_article(title="Unique Title A")
        cleaner.clean(a1)
        assert len(cleaner._seen_titles) > 0
        cleaner.reset()
        assert len(cleaner._seen_titles) == 0
        assert len(cleaner._seen_hashes) == 0
        assert len(cleaner._hash_buckets) == 0

    def test_reset_allows_reprocessing(self):
        cleaner = ContentCleaner()
        a1 = self._make_article(title="Reset Test")
        assert cleaner.clean(a1) is not None
        cleaner.reset()
        a2 = self._make_article(title="Reset Test", url="https://example.com/test2")
        assert cleaner.clean(a2) is not None


class TestSummarizerValidation:
    def test_valid_rewrite_accepted(self):
        assert Summarizer._is_valid_rewrite(
            "This is a completely rewritten version of the article with new wording.",
            "This is the original article content that needs to be rewritten."
        )

    def test_empty_rewrite_rejected(self):
        assert not Summarizer._is_valid_rewrite("", "Original content here.")

    def test_identical_rewrite_rejected(self):
        original = "This is the original text that should be modified by the LLM."
        assert not Summarizer._is_valid_rewrite(original, original)

    def test_invalid_pattern_rejected(self):
        assert not Summarizer._is_valid_rewrite(
            "I cannot rewrite this content",
            "Some original content here that is quite long and detailed."
        )

    def test_too_short_rewrite_rejected(self):
        long_original = "A" * 300
        assert not Summarizer._is_valid_rewrite(
            "Short",
            long_original,
        )

    def test_fallback_summary_no_api_key(self):
        summarizer = Summarizer(api_key=None)
        result = summarizer.summarize("Title", "Short content.")
        assert result == "Short content."

    def test_fallback_summary_truncation(self):
        summarizer = Summarizer(api_key=None)
        long_content = "A" * 500
        result = summarizer.summarize("Title", long_content)
        assert len(result) <= 203


class TestSafeUrl:
    def test_public_url_allowed(self):
        assert _is_safe_url("https://www.example.com/article")

    def test_localhost_blocked(self):
        assert not _is_safe_url("http://localhost/admin")

    def test_private_ip_blocked(self):
        assert not _is_safe_url("http://192.168.1.1/secret")

    def test_127_ip_blocked(self):
        assert not _is_safe_url("http://127.0.0.1/admin")

    def test_10_ip_blocked(self):
        assert not _is_safe_url("http://10.0.0.1/internal")

    def test_172_16_ip_blocked(self):
        assert not _is_safe_url("http://172.16.0.1/internal")

    def test_ftp_scheme_blocked(self):
        assert not _is_safe_url("ftp://example.com/file")

    def test_metadata_endpoint_blocked(self):
        assert not _is_safe_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_ipv6_loopback_blocked(self):
        assert not _is_safe_url("http://[::1]/admin")

    def test_no_hostname_blocked(self):
        assert not _is_safe_url("https:///path")
