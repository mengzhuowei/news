"""AI 摘要与重写模块 — 支持所有兼容 OpenAI 协议的模型"""

import os
import re
from typing import Optional, Tuple
from loguru import logger


class Summarizer:
    """文章摘要生成器 — 支持所有兼容 OpenAI 协议的模型"""

    REWRITE_SYSTEM_PROMPT = (
        "You are a professional news editor. Rewrite the following article to make it unique "
        "while preserving all key facts, data, and quotes. Keep the same language as the original. "
        "Change sentence structures, use synonyms, and rephrase paragraphs. "
        "Do NOT add information that isn't in the original. "
        "Output ONLY the rewritten article, no explanations."
    )

    REWRITE_SAFE_PROMPT = (
        "You are a professional news editor. Rewrite the following article in a neutral, factual tone. "
        "Keep the same language as the original. Focus strictly on objective facts, numbers, and data. "
        "Completely avoid any opinions, commentary, political references, or controversial statements. "
        "Output ONLY the rewritten article, no explanations."
    )

    # LLM 输出中的无效模式 — 命中任意一个则判定为失败
    _INVALID_PATTERNS = [
        r'\[Text not provided\]',
        r'Original:\s*\n\s*Rewrite:',
        r'^Title:.*Original:.*Rewrite:',
        r'I cannot rewrite',
        r'I\'m unable to rewrite',
        r'I don\'t have.*content',
        r'No content provided',
    ]

    @classmethod
    def _is_valid_rewrite(cls, text: str, original: str) -> bool:
        """校验 LLM 输出是否为有效重写，而非回显 prompt 或返回无效内容"""
        if not text or not text.strip():
            return False
        # 检查是否命中无效模式
        for pattern in cls._INVALID_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                return False
        # 检查是否与原文几乎相同（LLM 未做任何修改）
        clean_original = re.sub(r'\s+', ' ', original).strip()
        clean_text = re.sub(r'\s+', ' ', text).strip()
        if clean_text == clean_original:
            return False
        # 输出过短（少于 50 字符）且与原文差异大，可能是异常
        if len(clean_text) < 50 and len(clean_original) > 200:
            return False
        return True

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-3.5-turbo"
        self.max_summary_length = 200
        self._client: Optional[object] = None

    @property
    def client(self):
        """懒加载 OpenAI 客户端（复用连接）"""
        if self._client is None and self.api_key:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def summarize(self, title: str, content: str) -> str:
        """基础摘要（无 LLM）— 取首句截断"""
        if len(content) <= self.max_summary_length:
            return content
        summary = content.split('.')[0]
        if len(summary) > self.max_summary_length:
            summary = summary[:self.max_summary_length].rsplit(' ', 1)[0] + '...'
        return summary.strip()

    async def summarize_with_ai(self, title: str, content: str) -> str:
        """AI 摘要 — 调用 LLM 生成"""
        if not self.api_key:
            return self.summarize(title, content)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Create a concise summary in no more than 200 characters. "
                            "Focus on main points and key information."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Title: {title}\n\nContent: {content[:4000]}\n\nSummary:",
                    },
                ],
                max_tokens=150,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[Summarizer] AI failed: {e}, falling back")
            return self.summarize(title, content)

    async def rewrite(self, title: str, content: str) -> Tuple[str, str, bool]:
        """AI 重写（洗稿）— 返回 (rewritten_content, rewritten_summary, was_rewritten)"""
        if not self.api_key:
            return content, content[:200], False

        truncated = content[:4000]

        # 第一次尝试：标准 Prompt
        rewritten = await self._call_rewrite(self.client, title, truncated, self.REWRITE_SYSTEM_PROMPT)

        # 校验输出质量，不合格则用安全 Prompt 重试
        if not self._is_valid_rewrite(rewritten, truncated):
            logger.warning(f"[Summarizer] Standard rewrite invalid, retrying with safe prompt")
            rewritten = await self._call_rewrite(self.client, title, truncated, self.REWRITE_SAFE_PROMPT)

        # 仍不合格则返回原文
        if not self._is_valid_rewrite(rewritten, truncated):
            logger.warning(f"[Summarizer] Both rewrites invalid for '{title}', using original")
            return content, content[:200], False

        # 生成摘要
        summary = await self._call_summary(self.client, title, rewritten)
        if summary is None:
            summary = rewritten[:200]

        return rewritten, summary, True

    async def _call_rewrite(self, client, title: str, content: str, system_prompt: str) -> Optional[str]:
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Title: {title}\n\nOriginal:\n{content}\n\nRewrite:"},
                ],
                max_tokens=1500,
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"[Summarizer] Rewrite failed: {e}")
            return None

    async def _call_summary(self, client, title: str, content: str) -> Optional[str]:
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "1-2 sentence summary. Max 200 characters."},
                    {"role": "user", "content": f"Title: {title}\n\n{content[:2000]}\n\nSummary:"},
                ],
                max_tokens=100,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"[Summarizer] Summary failed: {e}")
            return None
