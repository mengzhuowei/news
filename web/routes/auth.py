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
