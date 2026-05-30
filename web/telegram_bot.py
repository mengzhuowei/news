"""Telegram Bot — 处理用户交互和命令"""

import os
from datetime import datetime, timezone
from loguru import logger

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        ContextTypes,
    )
    TELEGRAM_BOT_AVAILABLE = True
except ImportError:
    TELEGRAM_BOT_AVAILABLE = False
    logger.warning("[TelegramBot] python-telegram-bot not installed. Bot commands disabled.")

from web.database import SessionLocal
from web.models import Subscription
from web.telegram_service import TelegramService, _escape_html


# 可用分类
AVAILABLE_CATEGORIES = [
    "world", "technology", "business", "science",
    "finance", "markets", "ai", "startups",
]


class NewsTelegramBot:
    """新闻聚合 Telegram Bot"""

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.service = TelegramService()
        self.app = None

        if not TELEGRAM_BOT_AVAILABLE or not self.token:
            logger.warning("[TelegramBot] Bot not configured or dependencies missing")
            return

        self.app = Application.builder().token(self.token).build()
        self._setup_handlers()

    def _setup_handlers(self):
        """设置命令和回调处理器"""
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("stop", self.stop))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("settime", self.settime))
        self.app.add_handler(CommandHandler("categories", self.categories))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        chat_id = update.effective_chat.id
        user = update.effective_user

        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.chat_id == chat_id
            ).first()

            if subscription:
                if subscription.is_active:
                    # 已订阅用户
                    await update.message.reply_text(
                        f"👋 Welcome back, {user.first_name}!\n\n"
                        "You're already subscribed to news digest.\n"
                        "Use /status to view your subscription details."
                    )
                else:
                    # 之前取消订阅的用户，显示重新订阅选项
                    keyboard = [
                        [InlineKeyboardButton("✅ Reactivate", callback_data="reactivate")]
                    ]
                    await update.message.reply_text(
                        "You previously unsubscribed.\n"
                        "Would you like to reactivate your subscription?",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
            else:
                # 新用户，提示如何订阅
                await update.message.reply_text(
                    f"👋 Hello, {user.first_name}!\n\n"
                    "I'm a news aggregator bot that sends you daily news digests.\n\n"
                    "To subscribe, visit our website and click the subscribe button.\n"
                    "You'll be able to choose your preferred categories and push time."
                )
        finally:
            db.close()

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /stop 命令 — 取消订阅"""
        chat_id = update.effective_chat.id

        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.chat_id == chat_id
            ).first()

            if not subscription or not subscription.is_active:
                await update.message.reply_text(
                    "You're not currently subscribed.\n"
                    "Visit our website to subscribe."
                )
                return

            subscription.is_active = False
            db.commit()

            await self.service.send_unsubscribe_confirm(chat_id)
        finally:
            db.close()

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /status 命令 — 查看订阅状态"""
        chat_id = update.effective_chat.id

        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.chat_id == chat_id
            ).first()

            if not subscription or not subscription.is_active:
                await update.message.reply_text(
                    "You're not currently subscribed.\n"
                    "Visit our website to subscribe."
                )
                return

            categories = ", ".join(_escape_html(c) for c in subscription.categories) if subscription.categories else "all"
            push_time = subscription.push_time or "09:00:00"
            frequency = subscription.frequency or "daily"

            text = (
                "📋 <b>Subscription Status</b>\n\n"
                f"<b>Status:</b> Active\n"
                f"<b>Frequency:</b> {frequency}\n"
                f"<b>Push Time:</b> {push_time[:5]}\n"
                f"<b>Timezone:</b> {subscription.timezone}\n"
                f"<b>Categories:</b> {categories}\n"
            )

            await update.message.reply_text(text, parse_mode="HTML")
        finally:
            db.close()

    async def settime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /settime 命令 — 设置推送时间"""
        chat_id = update.effective_chat.id

        if not context.args or len(context.args) != 1:
            await update.message.reply_text(
                "Usage: /settime HH:MM\n"
                "Example: /settime 09:00"
            )
            return

        time_str = context.args[0]
        if len(time_str) != 5 or time_str[2] != ":":
            await update.message.reply_text(
                "Invalid format. Please use HH:MM\n"
                "Example: /settime 09:00"
            )
            return

        try:
            hour, minute = map(int, time_str.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Invalid time. Please use HH:MM format (00:00 - 23:59)"
            )
            return

        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.chat_id == chat_id
            ).first()

            if not subscription or not subscription.is_active:
                await update.message.reply_text(
                    "You're not currently subscribed.\n"
                    "Visit our website to subscribe."
                )
                return

            subscription.push_time = f"{time_str}:00"
            db.commit()

            await update.message.reply_text(
                f"✅ Push time set to <b>{time_str}</b>",
                parse_mode="HTML",
            )
        finally:
            db.close()

    async def categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /categories 命令 — 管理订阅分类"""
        chat_id = update.effective_chat.id

        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.chat_id == chat_id
            ).first()

            if not subscription or not subscription.is_active:
                await update.message.reply_text(
                    "You're not currently subscribed.\n"
                    "Visit our website to subscribe."
                )
                return

            current_cats = subscription.categories or []

            # 构建分类选择键盘
            keyboard = []
            for cat in AVAILABLE_CATEGORIES:
                emoji = "✅" if cat in current_cats else "⬜"
                keyboard.append([
                    InlineKeyboardButton(f"{emoji} {cat}", callback_data=f"cat_{cat}")
                ])

            keyboard.append([
                InlineKeyboardButton("Done", callback_data="cat_done")
            ])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "📂 <b>Select Categories</b>\n\n"
                "Click to toggle categories:",
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        finally:
            db.close()

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理回调查询"""
        query = update.callback_query
        chat_id = query.message.chat_id
        data = query.data

        if data.startswith("cat_"):
            await self._handle_category_callback(query, chat_id, data)
        elif data.startswith("confirm_"):
            await self._handle_confirm_callback(query, chat_id, data)
        elif data == "reactivate":
            await self._handle_reactivate_callback(query, chat_id)

        await query.answer()

    async def _handle_reactivate_callback(self, query, chat_id: int):
        """处理重新激活订阅回调"""
        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.chat_id == chat_id
            ).first()

            if not subscription or subscription.is_active:
                await query.edit_message_text("✅ Subscription is already active!")
                return

            subscription.is_active = True
            db.commit()

            await query.edit_message_text("🎉 Subscription reactivated!")
            await self.service.send_welcome(chat_id, subscription.categories or [])
        finally:
            db.close()

    async def _handle_category_callback(self, query, chat_id: int, data: str):
        """处理分类选择回调"""
        if data == "cat_done":
            await query.edit_message_text("✅ Categories updated!")
            return

        category = data[4:]  # 移除 "cat_" 前缀

        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.chat_id == chat_id
            ).first()

            if not subscription:
                return

            current_cats = list(subscription.categories or [])

            if category in current_cats:
                current_cats.remove(category)
            else:
                current_cats.append(category)

            subscription.categories = current_cats
            db.commit()

            # 更新键盘显示
            keyboard = []
            for cat in AVAILABLE_CATEGORIES:
                emoji = "✅" if cat in current_cats else "⬜"
                keyboard.append([
                    InlineKeyboardButton(f"{emoji} {cat}", callback_data=f"cat_{cat}")
                ])
            keyboard.append([
                InlineKeyboardButton("Done", callback_data="cat_done")
            ])

            await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
        finally:
            db.close()

    async def _handle_confirm_callback(self, query, chat_id: int, data: str):
        """处理确认订阅回调"""
        token = data[8:]  # 移除 "confirm_" 前缀

        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.confirm_token == token
            ).first()

            if not subscription:
                await query.edit_message_text("❌ Invalid or expired confirmation link.")
                return

            if subscription.confirmed_at and subscription.is_active:
                await query.edit_message_text("✅ Subscription already confirmed!")
                return

            # 激活订阅
            subscription.confirmed_at = datetime.now(timezone.utc)
            subscription.is_active = True
            subscription.chat_id = chat_id
            db.commit()

            await query.edit_message_text("✅ Subscription confirmed!")
            await self.service.send_welcome(chat_id, subscription.categories or [])
        finally:
            db.close()

    def run(self):
        """启动 Bot（长轮询模式，阻塞调用）"""
        if not self.app:
            logger.error("[TelegramBot] Cannot start: Bot not configured")
            return

        logger.info("[TelegramBot] Starting bot...")
        self.app.run_polling()


if __name__ == "__main__":
    bot = NewsTelegramBot()
    bot.run()
