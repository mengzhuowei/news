"""Telegram 推送调度器 — 定时发送新闻摘要给订阅用户"""

import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List
from loguru import logger

from web.database import SessionLocal
from web.models import Subscription, Article
from web.telegram_service import TelegramService


class PushScheduler:
    """Telegram 推送调度器"""

    def __init__(self, check_interval_seconds: int = 60):
        self.check_interval = check_interval_seconds
        self.telegram_service = TelegramService()
        self.articles_per_push = int(os.getenv("ARTICLES_PER_PUSH", "10"))
        self.site_baseurl = os.getenv("SITE_BASEURL", "").rstrip("/")

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
        """检查并发送到期的推送"""
        due_subscriptions = self._get_due_subscriptions()
        logger.info(f"[PushScheduler] Found {len(due_subscriptions)} due subscriptions")

        for sub in due_subscriptions:
            try:
                articles = self._get_recent_articles(sub)
                if articles:
                    success = await self.telegram_service.send_digest(
                        sub.chat_id, articles, sub.frequency
                    )
                    if success == "blocked":
                        # 用户屏蔽了 Bot，停用订阅
                        self._deactivate_subscription(sub.id)
                        logger.info(f"[PushScheduler] Deactivated blocked subscription {sub.chat_id}")
                    elif success:
                        self._update_last_pushed(sub.id)
                        logger.info(f"[PushScheduler] Sent digest to {sub.chat_id}")
                else:
                    logger.info(f"[PushScheduler] No articles for subscription {sub.chat_id}")
            except Exception as e:
                logger.error(f"[PushScheduler] Error processing subscription {sub.chat_id}: {e}")

    def _get_due_subscriptions(self) -> List[Subscription]:
        """获取需要推送的订阅"""
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)

            # 查询活跃的 Telegram 订阅
            subscriptions = db.query(Subscription).filter(
                Subscription.is_active == True,
                Subscription.push_channel == "telegram",
                Subscription.chat_id.isnot(None),
            ).all()

            due = []
            for sub in subscriptions:
                if self._is_due(sub, now):
                    due.append(sub)

            return due
        finally:
            db.close()

    def _is_due(self, subscription: Subscription, now: datetime) -> bool:
        """判断订阅是否需要推送（基于用户本地时间）"""
        push_time = subscription.push_time or "09:00:00"
        frequency = subscription.frequency or "daily"
        sub_tz_name = subscription.timezone or "UTC"

        # 将 UTC now 转换为用户本地时间
        try:
            from zoneinfo import ZoneInfo
            user_tz = ZoneInfo(sub_tz_name)
        except ImportError:
            # Python < 3.9 fallback
            user_tz = timezone.utc

        local_now = now.astimezone(user_tz)
        local_hour, local_min = local_now.hour, local_now.minute

        push_hour, push_min = map(int, push_time[:5].split(":"))
        push_minutes = push_hour * 60 + push_min
        current_minutes = local_hour * 60 + local_min

        # 如果当前时间不在推送时间的检查间隔内，跳过
        if abs(current_minutes - push_minutes) > self.check_interval // 60 + 1:
            return False

        # 检查是否已经推送过
        last_pushed = subscription.last_pushed_at
        if last_pushed:
            # 确保 last_pushed_at 是 aware datetime
            if last_pushed.tzinfo is None:
                last_pushed = last_pushed.replace(tzinfo=timezone.utc)

            if frequency == "daily":
                # 每天推送：上次推送必须在 20 小时前
                if (now - last_pushed) < timedelta(hours=20):
                    return False
            elif frequency == "weekly":
                # 每周推送：上次推送必须在 6 天前
                if (now - last_pushed) < timedelta(days=6):
                    return False

        return True

    def _get_recent_articles(self, subscription: Subscription) -> List[dict]:
        """获取自上次推送以来的文章"""
        db = SessionLocal()
        try:
            last_pushed = subscription.last_pushed_at
            categories = subscription.categories or []

            query = db.query(Article).filter(Article.is_rewritten == True)

            # 如果有上次推送时间，只获取之后的文章
            if last_pushed:
                if last_pushed.tzinfo is None:
                    last_pushed = last_pushed.replace(tzinfo=timezone.utc)
                query = query.filter(Article.created_at > last_pushed)

            # 如果指定了分类，按分类过滤
            if categories:
                query = query.filter(Article.category.in_(categories))

            # 按创建时间降序，限制数量
            articles = query.order_by(Article.created_at.desc()).limit(
                self.articles_per_push
            ).all()

            return [
                {
                    "title": a.title,
                    "summary": a.summary,
                    "url": self._build_permalink(a),
                    "source": a.source,
                    "category": a.category,
                }
                for a in articles
            ]
        finally:
            db.close()

    def _build_permalink(self, article: Article) -> str:
        """构建站内永久链接，回退到原始 URL"""
        if self.site_baseurl:
            category = article.category or "uncategorized"
            slug = article.slug or f"article-{article.id}"
            return f"{self.site_baseurl}/{category}/{slug}/"
        return article.url

    def _update_last_pushed(self, subscription_id: int):
        """更新最后推送时间"""
        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.id == subscription_id
            ).first()
            if subscription:
                subscription.last_pushed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[PushScheduler] Failed to update last_pushed: {e}")
        finally:
            db.close()

    def _deactivate_subscription(self, subscription_id: int):
        """停用订阅（用户屏蔽 Bot 时）"""
        db = SessionLocal()
        try:
            subscription = db.query(Subscription).filter(
                Subscription.id == subscription_id
            ).first()
            if subscription:
                subscription.is_active = False
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[PushScheduler] Failed to deactivate subscription: {e}")
        finally:
            db.close()

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
