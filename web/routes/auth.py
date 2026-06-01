"""共享认证工具 — 避免路由模块间代码重复"""

import hmac
from typing import Optional
from fastapi import HTTPException
from web.models import Subscription


def verify_token(subscription: Subscription, token: Optional[str]) -> None:
    """使用恒定时间比较验证 token，防止时序攻击"""
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Verification required. Provide token parameter or request a verification email.",
        )
    if not subscription.confirm_token or not hmac.compare_digest(
        subscription.confirm_token, token
    ):
        raise HTTPException(status_code=403, detail="Invalid token")


def verify_telegram_chat_id(subscription: Subscription, chat_id: Optional[int]) -> None:
    """验证 Telegram chat_id 是否与订阅记录匹配 — 替代 token 验证"""
    if chat_id is None:
        raise HTTPException(
            status_code=401,
            detail="chat_id verification required.",
        )
    if subscription.chat_id != chat_id:
        raise HTTPException(status_code=403, detail="chat_id does not match subscription")
