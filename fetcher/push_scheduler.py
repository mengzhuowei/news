"""Telegram 推送调度器 — 定时发送新闻摘要给订阅用户"""

import os
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple
from loguru import logger

from web.database import get_session
from web.models import Subscription, Article
from web.telegram_service import TelegramService

MAX_CONCURRENT_CHATS = 10
CHAT_RATE_LIMIT_DELAY = 0.05


class PushScheduler:
    """Telegram 推送调度器"""

    def __init__(self, check_interval_seconds: int = 60):
        self.check_interval = check_interval_seconds
        self.telegram_service = TelegramService()
        self.articles_per_push = int(os.getenv("ARTICLES_PER_PUSH", "10"))
        self.site_baseurl = os.getenv("SITE_BASEURL", "").rstrip("/")
        self._last_check_minute: dict[int, tuple] = {}

    async def run_continuously(self):
        """持续运行调度器"""
        logger.info(f"[PushScheduler] Starting, interval={self.check_interval}s")

        while True:
            try:
                await self._check_and_push()
            except Exception as e:
                logger.error(f"[PushScheduler] Error: {e}")

            await asyncio.sleep(self.check_interval)

    async def run_once(self):
        """单次运行（用于测试）"""
        logger.info("[PushScheduler] Running once...")
        await self._check_and_push()

    async def _check_and_push(self):
        """检查并发送到期的推送（按 chat_id 分组并发）"""
        due_subscriptions = self._get_due_subscriptions()
        logger.info(f"[PushScheduler] Found {len(due_subscriptions)} due subscriptions")

        chat_groups: Dict[int, List[dict]] = defaultdict(list)
        for sub in due_subscriptions:
            chat_groups[sub["chat_id"]].append(sub)

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHATS)

        async def push_chat(chat_id: int, subs: List[dict]):
            async with semaphore:
                for sub in subs:
                    try:
                        articles = await asyncio.to_thread(self._get_recent_articles_sync, sub)
                        if articles:
                            success = await self.telegram_service.send_digest(
                                chat_id, articles, sub["frequency"]
                            )
                            if success == "blocked":
                                self._deactivate_subscription(sub["id"])
                                logger.info(f"[PushScheduler] Deactivated blocked subscription {chat_id}")
                            elif success:
                                self._update_last_pushed(sub["id"])
                                logger.info(f"[PushScheduler] Sent digest to {chat_id}")
                        else:
                            logger.info(f"[PushScheduler] No articles for subscription {chat_id}")
                        await asyncio.sleep(CHAT_RATE_LIMIT_DELAY)
                    except Exception as e:
                        logger.error(f"[PushScheduler] Error processing subscription {chat_id}: {e}")

        tasks = [push_chat(chat_id, subs) for chat_id, subs in chat_groups.items()]
        await asyncio.gather(*tasks, return_exceptions=True)

    def _get_due_subscriptions(self) -> List[dict]:
        """获取需要推送的订阅（返回纯数据，避免 session 关闭后访问延迟加载属性）"""
        now = datetime.now(timezone.utc)
        with get_session() as db:
            subscriptions = db.query(Subscription).filter(
                Subscription.is_active == True,
                Subscription.push_channel == "telegram",
                Subscription.chat_id.isnot(None),
            ).all()

            due = []
            for sub in subscriptions:
                if self._is_due(sub, now):
                    due.append({
                        "id": sub.id,
                        "chat_id": sub.chat_id,
                        "frequency": sub.frequency,
                        "last_pushed_at": sub.last_pushed_at,
                        "categories": list(sub.categories) if sub.categories else [],
                    })
            return due

    def _is_due(self, subscription, now: datetime) -> bool:
        """判断订阅是否需要推送（基于用户本地时间，记录已检查的分钟防止遗漏）"""
        push_time = subscription.push_time or "09:00:00"
        frequency = subscription.frequency or "daily"
        sub_tz_name = subscription.timezone or "UTC"

        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            user_tz = ZoneInfo(sub_tz_name)
        except (ImportError, ZoneInfoNotFoundError, KeyError):
            user_tz = timezone.utc

        local_now = now.astimezone(user_tz)
        local_hour, local_min = local_now.hour, local_now.minute

        push_hour, push_min = map(int, push_time[:5].split(":"))
        push_minutes = push_hour * 60 + push_min
        current_minutes = local_hour * 60 + local_min

        sub_id = subscription.id if hasattr(subscription, 'id') else id(subscription)
        current_day = local_now.date().toordinal()
        last_key = (sub_id, current_day, frequency)

        if abs(current_minutes - push_minutes) > self.check_interval // 60 + 1:
            return False

        if self._last_check_minute.get(sub_id) == last_key:
            return False

        last_pushed = subscription.last_pushed_at
        if last_pushed:
            if last_pushed.tzinfo is None:
                last_pushed = last_pushed.replace(tzinfo=timezone.utc)

            if frequency == "daily":
                if (now - last_pushed) < timedelta(hours=20):
                    return False
            elif frequency == "weekly":
                if (now - last_pushed) < timedelta(days=6):
                    return False

        self._last_check_minute[sub_id] = last_key
        return True

    def _get_recent_articles(self, sub: dict) -> List[dict]:
        """获取自上次推送以来的文章"""
        return self._get_recent_articles_sync(sub)

    def _get_recent_articles_sync(self, sub: dict) -> List[dict]:
        with get_session() as db:
            last_pushed = sub.get("last_pushed_at")
            categories = sub.get("categories", [])

            query = db.query(Article).filter(Article.is_rewritten == True)

            if last_pushed:
                if last_pushed.tzinfo is None:
                    last_pushed = last_pushed.replace(tzinfo=timezone.utc)
                query = query.filter(Article.created_at > last_pushed)

            if categories:
                query = query.filter(Article.category.in_(categories))

            articles = query.order_by(Article.created_at.desc()).limit(
                self.articles_per_push
            ).all()

            result = []
            for a in articles:
                article_url = self._build_permalink(a)
                logger.debug(f"[PushScheduler] article url: {article_url}")
                result.append({
                    "title": a.title,
                    "summary": a.summary,
                    "url": article_url,
                    "source": a.source,
                    "category": a.category,
                })
            return result

    def _build_permalink(self, article: Article) -> str:
        """构建站内永久链接，回退到原始 URL"""
        if self.site_baseurl:
            category = article.category or "uncategorized"
            slug = article.slug or f"article-{article.id}"
            return f"{self.site_baseurl}/{category}/{slug}/"
        return article.url

    def _update_last_pushed(self, subscription_id: int):
        """更新最后推送时间"""
        try:
            with get_session() as db:
                subscription = db.query(Subscription).filter(
                    Subscription.id == subscription_id
                ).first()
                if subscription:
                    subscription.last_pushed_at = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"[PushScheduler] Failed to update last_pushed: {e}")

    def _deactivate_subscription(self, subscription_id: int):
        """停用订阅（用户屏蔽 Bot 时）"""
        try:
            with get_session() as db:
                subscription = db.query(Subscription).filter(
                    Subscription.id == subscription_id
                ).first()
                if subscription:
                    subscription.is_active = False
        except Exception as e:
            logger.error(f"[PushScheduler] Failed to deactivate subscription: {e}")

    async def shutdown(self):
        """关闭资源"""
        await self.telegram_service.close()


async def main():
    """独立运行推送调度器"""
    import sys
    from web.database import init_db

    init_db()

    interval = int(os.getenv("PUSH_CHECK_INTERVAL", "60"))
    scheduler = PushScheduler(check_interval_seconds=interval)

    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--once":
            await scheduler.run_once()
        else:
            await scheduler.run_continuously()
    finally:
        await scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
