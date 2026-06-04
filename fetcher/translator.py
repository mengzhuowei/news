"""多语言翻译模块 — 将新闻标题、摘要、正文翻译为 6 种语言"""

import os
import json
import re
from typing import Optional, Dict
from dataclasses import dataclass
from loguru import logger


# 支持的目标语言
SUPPORTED_LANGUAGES = {
    "zh_cn": "简体中文 (Simplified Chinese)",
    "zh_tw": "繁體中文 (Traditional Chinese)",
    "ja":    "日本語 (Japanese)",
    "ru":    "Русский (Russian)",
    "de":    "Deutsch (German)",
    "fr":    "Français (French)",
}

TRANSLATE_SYSTEM_PROMPT = (
    "You are a professional news translator. Translate the given text accurately and naturally "
    "into the target language. Preserve all factual information, names, dates, and numbers. "
    "Do NOT add any commentary or explanation. Output ONLY the translated text."
)


@dataclass
class TranslationResult:
    """单篇文章的翻译结果"""
    title: Dict[str, str]   # {lang_code: translated_title}
    summary: Dict[str, str] # {lang_code: translated_summary}
    content: Dict[str, str] # {lang_code: translated_content}


class Translator:
    """多语言翻译器 — 复用 OpenAI 兼容协议的 LLM"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_concurrent: int = 3,
    ):
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-3.5-turbo"
        self.max_concurrent = max_concurrent
        self._client: Optional[object] = None

    @property
    def client(self):
        """懒加载 OpenAI 客户端"""
        if self._client is None and self.api_key:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    async def translate_text(self, text: str, target_lang: str, source_lang: str = "en") -> Optional[str]:
        """将文本翻译为目标语言"""
        if not self.api_key or not text or not text.strip():
            return None

        lang_name = SUPPORTED_LANGUAGES.get(target_lang, target_lang)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Translate the following text from {source_lang} to {lang_name}.\n\n"
                            f"Text:\n{text[:4000]}"
                        ),
                    },
                ],
                max_tokens=2000,
                temperature=0.3,
            )
            result = response.choices[0].message.content.strip()
            if self._is_valid_translation(result, text):
                return result
            logger.warning(f"[Translator] Invalid translation for {target_lang}, skipping")
            return None
        except Exception as e:
            logger.error(f"[Translator] Translation to {target_lang} failed: {e}")
            return None

    async def translate_article(self, title: str, summary: str, content: str) -> TranslationResult:
        """翻译一篇文章到所有目标语言"""
        result = TranslationResult(title={}, summary={}, content={})

        if not self.api_key:
            logger.warning("[Translator] No API key configured, skipping translation")
            return result

        # 批量翻译：每种语言一次性翻译 title + summary + content，减少 API 调用次数
        for lang_code, lang_name in SUPPORTED_LANGUAGES.items():
            try:
                translated = await self._translate_article_to_lang(
                    title=title,
                    summary=summary,
                    content=content,
                    lang_code=lang_code,
                    lang_name=lang_name,
                )
                if translated:
                    result.title[lang_code] = translated.get("title", "")
                    result.summary[lang_code] = translated.get("summary", "")
                    result.content[lang_code] = translated.get("content", "")
                    logger.debug(f"[Translator] Translated to {lang_code}: {title[:50]}...")
            except Exception as e:
                logger.error(f"[Translator] Failed to translate to {lang_code}: {e}")

        return result

    async def _translate_article_to_lang(
        self, title: str, summary: str, content: str, lang_code: str, lang_name: str,
    ) -> Optional[Dict[str, str]]:
        """将一篇文章翻译为指定语言 — 一次 API 调用完成 title/summary/content"""
        # 构建翻译请求，使用 JSON 格式输出以便解析
        user_content = (
            f"Translate the following news article to {lang_name}.\n\n"
            f"Title: {title}\n\n"
            f"Summary: {summary[:500]}\n\n"
            f"Content: {content[:3000]}\n\n"
            f"Output the translation as a JSON object with exactly these keys: "
            f'"title", "summary", "content". Output ONLY the JSON, no other text.'
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a professional news translator. "
                            "Translate the given news article into the target language. "
                            "Preserve all factual information, names, dates, and numbers. "
                            "Output ONLY a valid JSON object with keys: \"title\", \"summary\", \"content\". "
                            "No markdown, no code fences, no explanation."
                        ),
                    },
                    {"role": "user", "content": user_content},
                ],
                max_tokens=3000,
                temperature=0.3,
            )
            raw = response.choices[0].message.content.strip()

            # 清理可能的 markdown 代码块包裹
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

            parsed = json.loads(raw)

            # 校验必需字段
            if not all(k in parsed for k in ("title", "summary", "content")):
                logger.warning(f"[Translator] Missing keys in translation JSON for {lang_code}")
                return None

            # 校验翻译质量
            if not self._is_valid_translation(parsed["title"], title):
                logger.warning(f"[Translator] Invalid title translation for {lang_code}")
                return None

            return parsed

        except json.JSONDecodeError as e:
            logger.warning(f"[Translator] JSON parse error for {lang_code}: {e}")
            # JSON 解析失败，退回逐字段翻译
            return await self._fallback_translate(title, summary, content, lang_code, lang_name)
        except Exception as e:
            logger.error(f"[Translator] Batch translate to {lang_code} failed: {e}")
            return None

    async def _fallback_translate(
        self, title: str, summary: str, content: str, lang_code: str, lang_name: str,
    ) -> Optional[Dict[str, str]]:
        """逐字段翻译降级方案"""
        title_tr = await self.translate_text(title, lang_code)
        summary_tr = await self.translate_text(summary, lang_code)
        content_tr = await self.translate_text(content, lang_code)

        if not title_tr:
            return None

        return {
            "title": title_tr or "",
            "summary": summary_tr or "",
            "content": content_tr or "",
        }

    @staticmethod
    def _is_valid_translation(translated: str, original: str) -> bool:
        """校验翻译结果是否有效"""
        if not translated or not translated.strip():
            return False
        # 翻译结果与原文完全相同（未翻译）
        clean_orig = re.sub(r'\s+', ' ', original).strip()
        clean_trans = re.sub(r'\s+', ' ', translated).strip()
        if clean_trans == clean_orig:
            return False
        # 翻译结果过短
        if len(clean_trans) < 5:
            return False
        return True
