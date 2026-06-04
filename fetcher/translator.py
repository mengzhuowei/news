"""多语言翻译模块 — 支持所有兼容 OpenAI 协议的模型"""

import os
from typing import Optional
from loguru import logger

# 支持的目标语言
SUPPORTED_LANGUAGES = {
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "ja":    "日本語",
    "ru":    "Русский",
    "de":    "Deutsch",
    "fr":    "Français",
}

TRANSLATE_SYSTEM_PROMPT = (
    "You are a professional translator. Translate the following text into {target_language}. "
    "Preserve all key facts, data, quotes, and formatting. "
    "Do NOT add any information that isn't in the original. "
    "Output ONLY the translated text, no explanations or notes."
)

TRANSLATE_TITLE_PROMPT = (
    "Translate the following news title into {target_language}. "
    "Output ONLY the translated title, nothing else."
)

TRANSLATE_SUMMARY_PROMPT = (
    "Translate the following news summary into {target_language}. "
    "Preserve all key facts and data. "
    "Output ONLY the translated summary, nothing else."
)


class Translator:
    """多语言翻译器 — 异步调用 LLM 进行翻译"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        languages: Optional[list[str]] = None,
        concurrent: int = 3,
    ):
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-3.5-turbo"
        self.concurrent = concurrent
        self._client: Optional[object] = None

        # 从环境变量读取目标语言，默认全部
        env_langs = os.getenv("TRANSLATION_LANGUAGES", "")
        if languages:
            self.languages = languages
        elif env_langs:
            self.languages = [lang.strip() for lang in env_langs.split(",") if lang.strip()]
        else:
            self.languages = list(SUPPORTED_LANGUAGES.keys())

        # 校验语言代码
        invalid = [lang for lang in self.languages if lang not in SUPPORTED_LANGUAGES]
        if invalid:
            logger.warning(f"[Translator] Unsupported languages will be skipped: {invalid}")
            self.languages = [lang for lang in self.languages if lang in SUPPORTED_LANGUAGES]

    @property
    def client(self):
        """懒加载 OpenAI 客户端"""
        if self._client is None and self.api_key:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    @property
    def enabled(self) -> bool:
        """翻译功能是否启用（需要 API Key 且至少有一种目标语言）"""
        return bool(self.api_key and self.languages)

    async def translate_article(
        self, title: str, content: str, summary: str = "",
    ) -> dict[str, dict[str, str]]:
        """翻译一篇文章到所有目标语言，返回 {lang_code: {title, content, summary}}"""
        if not self.enabled:
            return {}

        translations: dict[str, dict[str, str]] = {}

        for lang_code in self.languages:
            lang_name = SUPPORTED_LANGUAGES[lang_code]
            try:
                translated_title = await self._translate_text(
                    title, lang_name, TRANSLATE_TITLE_PROMPT, max_tokens=300,
                )
                translated_content = await self._translate_text(
                    content[:4000], lang_name, TRANSLATE_SYSTEM_PROMPT, max_tokens=2000,
                )
                translated_summary = ""
                if summary:
                    translated_summary = await self._translate_text(
                        summary, lang_name, TRANSLATE_SUMMARY_PROMPT, max_tokens=300,
                    )

                translations[lang_code] = {
                    "title": translated_title or title,
                    "content": translated_content or content,
                    "summary": translated_summary or summary,
                }
                logger.debug(f"[Translator] Translated to {lang_code}: {title[:40]}...")

            except Exception as e:
                logger.error(f"[Translator] Failed to translate to {lang_code}: {e}")
                # 单语言失败不影响其他语言，跳过该语言
                continue

        return translations

    async def _translate_text(
        self, text: str, target_language: str, prompt_template: str, max_tokens: int = 1500,
    ) -> Optional[str]:
        """调用 LLM 翻译单段文本"""
        if not text or not text.strip():
            return text

        if not self.client:
            return None

        try:
            system_prompt = prompt_template.format(target_language=target_language)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            result = response.choices[0].message.content.strip()
            if not result:
                return None
            return result
        except Exception as e:
            logger.warning(f"[Translator] LLM call failed: {e}")
            return None
