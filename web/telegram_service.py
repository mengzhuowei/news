"""Telegram Bot API 客户端 — 负责发送消息和格式化内容"""

import os
from typing import Optional, List, Union
from datetime import datetime
from loguru import logger


class TelegramService:
    """Telegram Bot API 客户端"""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.api_url = f"https://api.telegram.org/bot{self.token}" if self.token else None
        self.enabled = bool(self.token)
        self._client = None

    @property
    def client(self):
        """懒加载 httpx 客户端"""
        if self._client is None and self.enabled:
            import httpx
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """关闭客户端连接"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[dict] = None,
    ) -> Union[bool, str]:
        """发送消息到 Telegram
        Returns: True=成功, False=失败, "blocked"=用户屏蔽了Bot
        """
        if not self.enabled:
            logger.warning("[Telegram] Service not enabled, skipping")
            return False

        try:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            response = await self.client.post(f"{self.api_url}/sendMessage", json=payload)
            if response.status_code != 200:
                logger.error(f"[Telegram] HTTP {response.status_code}: {response.text[:200]}")
                return False
            data = response.json()

            if data.get("ok"):
                logger.info(f"[Telegram] Message sent to {chat_id}")
                return True
            else:
                error_code = data.get("error_code", "unknown")
                description = data.get("description", "")
                logger.error(f"[Telegram] Send failed: {error_code} - {description}")

                # 用户屏蔽了 Bot
                if error_code == 403 and "Forbidden" in description:
                    return "blocked"

                return False
        except Exception as e:
            logger.error(f"[Telegram] Error: {e}")
            return False

    async def send_confirmation(self, chat_id: int, token: str, base_url: str) -> Union[bool, str]:
        """发送订阅确认消息，带确认按钮"""
        confirm_url = f"{base_url}/api/telegram/confirm/{token}"

        text = (
            "📰 <b>Confirm Your Subscription</b>\n\n"
            "Click the button below to confirm your subscription to news digest."
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Confirm Subscription",
                        "url": confirm_url,
                    }
                ]
            ]
        }

        return await self.send_message(chat_id, text, reply_markup=reply_markup)

    async def send_welcome(self, chat_id: int, categories: List[str]) -> Union[bool, str]:
        """发送欢迎消息"""
        cats = ", ".join(_escape_html(c) for c in categories) if categories else "all categories"
        text = (
            "🎉 <b>Welcome!</b>\n\n"
            f"Your subscription is confirmed. You'll receive news from: <i>{cats}</i>\n\n"
            "Commands:\n"
            "/status - View subscription status\n"
            "/settime HH:MM - Set push time\n"
            "/categories - Manage categories\n"
            "/stop - Unsubscribe"
        )
        return await self.send_message(chat_id, text)

    async def send_unsubscribe_confirm(self, chat_id: int) -> Union[bool, str]:
        """发送取消订阅确认"""
        text = (
            "👋 <b>Unsubscribed</b>\n\n"
            "You've been unsubscribed from news digest.\n"
            "To resubscribe, visit our website."
        )
        return await self.send_message(chat_id, text)

    async def send_digest(self, chat_id: int, articles: List[dict], frequency: str) -> Union[bool, str]:
        """发送新闻摘要"""
        if not articles:
            return False

        date_str = datetime.now().strftime("%B %d, %Y")
        digest_title = "Daily" if frequency == "daily" else "Weekly"
        message = f"📰 *{digest_title} News Digest*\n_{date_str}_\n\n"

        for i, article in enumerate(articles[:10], 1):
            source = article.get("source", "")
            category = article.get("category", "")
            title = article.get("title", "Untitled")
            summary = article.get("summary", "")
            url = article.get("url", "")

            logger.debug(f"[Telegram] article[{i}] url: {url}")

            if len(summary) > 150:
                summary = summary[:147] + "..."

            message += f"*{i}\\.\ {_escape_markdown(title)}*\n"
            message += f"_{_escape_markdown(source + ' | ' + category)}_\n"
            if summary:
                message += f"{_escape_markdown(summary)}\n"
            if url:
                message += f"[Read more]({url})\n"
            message += "\n"

        message += "\-\-\-\n_Sent by News Aggregator Bot_"

        return await self.send_message(chat_id, message, parse_mode="MarkdownV2")

    async def set_my_commands(self, commands: List[dict]) -> bool:
        """设置 Bot 命令菜单"""
        if not self.enabled:
            return False

        try:
            response = await self.client.post(
                f"{self.api_url}/setMyCommands",
                json={"commands": commands},
            )
            if response.status_code != 200:
                return False
            data = response.json()
            return data.get("ok", False)
        except Exception as e:
            logger.error(f"[Telegram] Set commands failed: {e}")
            return False


def _escape_html(text: str) -> str:
    """转义 Telegram HTML 特殊字符"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _escape_markdown(text: str) -> str:
    """转义 Telegram MarkdownV2 特殊字符"""
    special_chars = [
        '_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|',
        '{', '}', '.', '!',
    ]
    result = text
    for char in special_chars:
        result = result.replace(char, f"\\{char}")
    return result
