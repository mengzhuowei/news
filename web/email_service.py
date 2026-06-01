"""邮件发送服务 — 基于 SendGrid"""

import asyncio
import html as html_module
import os
from typing import Optional, List
from loguru import logger

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    SENDGRID_AVAILABLE = True
except ImportError:
    SENDGRID_AVAILABLE = False


class EmailService:
    """邮件发送服务 — 基于 SendGrid"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SENDGRID_API_KEY")
        self.from_email = os.getenv("EMAIL_FROM", "noreply@newsaggregator.com")
        self.enabled = bool(self.api_key) and SENDGRID_AVAILABLE
        self._sg = None

        if self.api_key and not SENDGRID_AVAILABLE:
            logger.warning("[Email] sendgrid package not installed, email service disabled")

    @property
    def sg(self):
        """懒加载 SendGrid 客户端（复用连接）"""
        if self._sg is None and self.enabled:
            self._sg = SendGridAPIClient(self.api_key)
        return self._sg

    async def send_email(
        self, to_email: str, subject: str, html_content: str,
        subscription_id: Optional[int] = None, email_type: Optional[str] = None,
    ) -> bool:
        """发送邮件 — SendGrid 同步 API 包装为异步，发送后记录 EmailLog"""
        if not self.enabled:
            logger.warning(f"[Email] Service not enabled, skipping: {to_email}")
            self._save_email_log(subscription_id, email_type, 'failed')
            return False

        try:
            message = Mail(
                from_email=self.from_email,
                to_emails=to_email,
                subject=subject,
                html_content=html_content,
            )

            response = await asyncio.to_thread(self.sg.send, message)

            if response.status_code in [200, 201, 202]:
                logger.info(f"[Email] Sent to {to_email}")
                self._save_email_log(subscription_id, email_type, 'sent')
                return True
            self._save_email_log(subscription_id, email_type, 'failed')
            return False
        except Exception as e:
            logger.error(f"[Email] Error: {e}")
            self._save_email_log(subscription_id, email_type, 'failed')
            return False

    def _save_email_log(
        self, subscription_id: Optional[int], email_type: Optional[str], status: str,
    ):
        """记录邮件发送日志到数据库"""
        if not email_type:
            return
        try:
            from web.database import get_session
            from web.models import EmailLog

            with get_session() as db:
                log = EmailLog(
                    subscription_id=subscription_id,
                    email_type=email_type,
                    status=status,
                )
                db.add(log)
        except Exception as e:
            logger.error(f"EmailLog save error: {e}")

    async def send_confirmation_email(
        self, email: str, token: str,
        subscription_id: Optional[int] = None,
        site_url: Optional[str] = None,
    ) -> bool:
        """发送订阅确认邮件"""
        base_url = site_url or os.getenv("SITE_BASEURL", "https://localhost:8000")
        confirm_url = f"{base_url}/api/subscribe/confirm/{token}"
        html = f"""
        <html><body style="font-family: Arial; max-width: 600px; margin: 0 auto;">
            <h2>Confirm Your Subscription</h2>
            <p>Click the button below to confirm:</p>
            <a href="{confirm_url}" style="background: #e94560; color: white;
               padding: 12px 24px; text-decoration: none; border-radius: 4px;
               display: inline-block;">Confirm Subscription</a>
        </body></html>
        """
        return await self.send_email(
            email, "Confirm Your Subscription", html,
            subscription_id=subscription_id, email_type='confirmation',
        )

    async def send_welcome_email(
        self, email: str, categories: Optional[List[str]] = None,
        subscription_id: Optional[int] = None,
    ) -> bool:
        """发送欢迎邮件"""
        cats = html_module.escape(", ".join(categories)) if categories else "all categories"
        html = f"""
        <html><body style="font-family: Arial; max-width: 600px; margin: 0 auto;">
            <h2>Welcome!</h2>
            <p>Your subscription is confirmed. You'll receive news from {cats}.</p>
        </body></html>
        """
        return await self.send_email(
            email, "Welcome to News Aggregator!", html,
            subscription_id=subscription_id, email_type='welcome',
        )

    async def send_unsubscribe_confirmation(
        self, email: str, subscription_id: Optional[int] = None,
    ) -> bool:
        """发送取消订阅确认"""
        html = """
        <html><body style="font-family: Arial; max-width: 600px; margin: 0 auto;">
            <h2>Unsubscribed</h2>
            <p>Your subscription has been cancelled.</p>
        </body></html>
        """
        return await self.send_email(
            email, "Unsubscribed Successfully", html,
            subscription_id=subscription_id, email_type='unsubscribe',
        )
