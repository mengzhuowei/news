"""扩展单元测试 — 覆盖路由验证、认证、推送调度等关键逻辑"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


from fetcher.cleaner import SimHash, ContentCleaner
from fetcher.summarizer import Summarizer
from fetcher.content_enricher import _is_safe_url
from fetcher.fetchers.base import Article
from fetcher.scheduler import make_slug
from web.routes.auth import verify_token, verify_telegram_chat_id
from web.routes.validators import validate_push_time, validate_frequency
from web.models import Subscription


class TestMakeSlug:
    def test_basic_slug(self):
        slug = make_slug("BBC News", "Hello World Article", 42)
        assert "bbcnews" in slug
        assert "42" in slug

    def test_special_characters_removed(self):
        slug = make_slug("Re:uters!", "Title with $pecial chars", 1)
        assert "$" not in slug
        assert "!" not in slug

    def test_uniqueness_with_id(self):
        slug1 = make_slug("Source", "Same Title", 1)
        slug2 = make_slug("Source", "Same Title", 2)
        assert slug1 != slug2


class TestValidators:
    def test_valid_push_time(self):
        assert validate_push_time(None, "09:00:00") == "09:00:00"

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            validate_push_time(None, "9:00")

    def test_invalid_hour(self):
        with pytest.raises(ValueError):
            validate_push_time(None, "25:00:00")

    def test_invalid_minute(self):
        with pytest.raises(ValueError):
            validate_push_time(None, "09:60:00")

    def test_none_passes(self):
        assert validate_push_time(None, None) is None

    def test_valid_frequency(self):
        assert validate_frequency(None, "daily") == "daily"
        assert validate_frequency(None, "weekly") == "weekly"

    def test_invalid_frequency(self):
        with pytest.raises(ValueError):
            validate_frequency(None, "monthly")

    def test_none_frequency(self):
        assert validate_frequency(None, None) is None


class TestVerifyToken:
    def test_valid_token(self):
        sub = Subscription(chat_id=123, confirm_token="abc123")
        verify_token(sub, "abc123")

    def test_invalid_token(self):
        from fastapi import HTTPException
        sub = Subscription(chat_id=123, confirm_token="abc123")
        with pytest.raises(HTTPException) as exc_info:
            verify_token(sub, "wrong")
        assert exc_info.value.status_code == 403

    def test_missing_token(self):
        from fastapi import HTTPException
        sub = Subscription(chat_id=123, confirm_token="abc123")
        with pytest.raises(HTTPException) as exc_info:
            verify_token(sub, None)
        assert exc_info.value.status_code == 401


class TestVerifyTelegramChatId:
    def test_matching_chat_id(self):
        sub = Subscription(chat_id=123)
        verify_telegram_chat_id(sub, 123)

    def test_mismatched_chat_id(self):
        from fastapi import HTTPException
        sub = Subscription(chat_id=123)
        with pytest.raises(HTTPException) as exc_info:
            verify_telegram_chat_id(sub, 456)
        assert exc_info.value.status_code == 403

    def test_none_chat_id(self):
        from fastapi import HTTPException
        sub = Subscription(chat_id=123)
        with pytest.raises(HTTPException) as exc_info:
            verify_telegram_chat_id(sub, None)
        assert exc_info.value.status_code == 401


class TestSubscriptionModel:
    def test_requires_email_or_chat_id(self):
        with pytest.raises(ValueError):
            Subscription()

    def test_valid_with_email(self):
        sub = Subscription(email="test@example.com")
        assert sub.email == "test@example.com"

    def test_valid_with_chat_id(self):
        sub = Subscription(chat_id=123)
        assert sub.chat_id == 123


class TestSafeUrlExtended:
    def test_link_local_blocked(self):
        assert not _is_safe_url("http://169.254.1.1/metadata")

    def test_ipv4_mapped_ipv6_blocked(self):
        assert not _is_safe_url("http://[::ffff:127.0.0.1]/admin")

    def test_fc00_ipv6_blocked(self):
        assert not _is_safe_url("http://[fc00::1]/internal")

    def test_https_public_allowed(self):
        assert _is_safe_url("https://www.reuters.com/article")

    def test_javascript_scheme_blocked(self):
        assert not _is_safe_url("javascript:alert(1)")


class TestSimHashEdgeCases:
    def test_single_word_text(self):
        sh = SimHash()
        h = sh.compute("hello")
        assert isinstance(h, int)

    def test_two_word_text(self):
        sh = SimHash()
        h = sh.compute("hello world")
        assert isinstance(h, int)

    def test_very_long_text(self):
        sh = SimHash()
        text = "word " * 10000
        h = sh.compute(text)
        assert isinstance(h, int)

    def test_unicode_text(self):
        sh = SimHash()
        h = sh.compute("你好世界，这是一个测试")
        assert isinstance(h, int)


class TestContentCleanerEdgeCases:
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

    def test_script_tags_removed(self):
        cleaner = ContentCleaner()
        article = self._make_article(content='<p>Hello</p><script>alert("xss")</script>')
        result = cleaner.clean(article)
        assert result is not None
        assert "<script>" not in result.content
        assert "alert" not in result.content

    def test_style_tags_removed(self):
        cleaner = ContentCleaner()
        article = self._make_article(content='<p>Hello</p><style>body{color:red}</style>')
        result = cleaner.clean(article)
        assert result is not None
        assert "<style>" not in result.content

    def test_similar_content_dedup(self):
        cleaner = ContentCleaner()
        a1 = self._make_article(title="News: Market rises 5%", content="The stock market rose 5% today on strong earnings reports.")
        a2 = self._make_article(title="News: Market rises 5%", content="The stock market rose 5% today on strong earnings reports!", url="https://example.com/test2")
        assert cleaner.clean(a1) is not None
        assert cleaner.clean(a2) is None

    def test_ads_removed(self):
        cleaner = ContentCleaner()
        article = self._make_article(content="Real content. Subscribe to our newsletter now! Cookie Policy here.")
        result = cleaner.clean(article)
        assert result is not None
        assert "Subscribe to" not in result.content
        assert "Cookie" not in result.content


class TestSummarizerValidationExtended:
    def test_text_not_provided_pattern(self):
        assert not Summarizer._is_valid_rewrite(
            "[Text not provided]",
            "Original content that is quite long and detailed."
        )

    def test_i_cannot_rewrite_pattern(self):
        assert not Summarizer._is_valid_rewrite(
            "I cannot rewrite this content as requested.",
            "Original content that is quite long and detailed."
        )

    def test_whitespace_only_rejected(self):
        assert not Summarizer._is_valid_rewrite(
            "   \n\t  ",
            "Original content here."
        )

    def test_valid_rewrite_with_different_wording(self):
        original = "The quick brown fox jumped over the lazy dog on a sunny afternoon."
        rewritten = "A fast dark-colored fox leaped across the sleepy canine during bright daylight."
        assert Summarizer._is_valid_rewrite(rewritten, original)
