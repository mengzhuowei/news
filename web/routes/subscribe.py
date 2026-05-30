"""订阅 API — 创建/确认/取消订阅"""

import secrets
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from web.database import get_db
from web.models import Subscription
from web.email_service import EmailService
from web.routes.auth import verify_token

router = APIRouter(prefix="/api/subscribe", tags=["subscription"])


def _get_email_service() -> EmailService:
    """懒加载 EmailService 实例"""
    if not hasattr(_get_email_service, '_instance'):
        _get_email_service._instance = EmailService()
    return _get_email_service._instance


def _safe_subscription_response(sub: Subscription) -> dict:
    """序列化订阅信息，排除 confirm_token 等敏感字段"""
    return {
        "id": sub.id,
        "email": sub.email,
        "is_active": sub.is_active,
        "frequency": sub.frequency,
        "site": sub.site,
        "categories": sub.categories if sub.categories else [],
        "confirmed_at": sub.confirmed_at.isoformat() if sub.confirmed_at else None,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


class SubscribeRequest(BaseModel):
    email: EmailStr
    frequency: str = "daily"
    site: Optional[str] = None
    categories: List[str] = []

    @field_validator('frequency')
    @classmethod
    def validate_frequency(cls, v):
        if v not in ('daily', 'weekly'):
            raise ValueError('frequency must be daily or weekly')
        return v


@router.post("/")
async def subscribe(request: SubscribeRequest, db: Session = Depends(get_db)):
    """创建订阅 → 发送确认邮件"""
    existing = db.query(Subscription).filter(Subscription.email == request.email).first()

    if existing:
        if not existing.is_active:
            existing.confirm_token = secrets.token_urlsafe(32)
            existing.frequency = request.frequency
            existing.site = request.site
            existing.categories = request.categories
            db.commit()
            await _get_email_service().send_confirmation_email(
                existing.email, existing.confirm_token, subscription_id=existing.id,
            )
        # 对所有已存在的邮箱返回相同消息，防止邮件枚举
        return {"message": "If this email is subscribed, you will receive a confirmation."}

    subscription = Subscription(
        email=request.email,
        frequency=request.frequency,
        site=request.site,
        categories=request.categories,
        confirm_token=secrets.token_urlsafe(32),
        is_active=False,  # 仅在邮件确认后激活
    )
    db.add(subscription)
    try:
        db.commit()
    except IntegrityError:
        # 并发请求导致唯一约束冲突，返回通用消息
        db.rollback()
        return {"message": "If this email is subscribed, you will receive a confirmation."}
    db.refresh(subscription)

    await _get_email_service().send_confirmation_email(
        subscription.email, subscription.confirm_token, subscription_id=subscription.id,
    )
    return _safe_subscription_response(subscription)


@router.get("/confirm/{token}")
async def confirm_subscription(token: str, db: Session = Depends(get_db)):
    """确认订阅 → 发送欢迎邮件"""
    subscription = db.query(Subscription).filter(Subscription.confirm_token == token).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Invalid token")

    if subscription.confirmed_at:
        # 即使已确认，如果之前被取消过，重新激活
        if not subscription.is_active:
            subscription.is_active = True
            db.commit()
        return {"status": "already_confirmed", "email": subscription.email}

    subscription.confirmed_at = datetime.now(timezone.utc)
    subscription.is_active = True
    # confirm_token 保留，用于后续身份验证（查询/更新/取消订阅）
    db.commit()

    categories = subscription.categories if subscription.categories else []
    await _get_email_service().send_welcome_email(
        subscription.email, categories, subscription_id=subscription.id,
    )
    return {"status": "confirmed", "email": subscription.email}


@router.delete("/{email}")
async def unsubscribe(
    email: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """取消订阅（需 token 或邮件验证）"""
    subscription = db.query(Subscription).filter(Subscription.email == email).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Not found")

    verify_token(subscription, token)

    subscription.is_active = False
    db.commit()
    await _get_email_service().send_unsubscribe_confirmation(
        subscription.email, subscription_id=subscription.id,
    )
    return {"status": "unsubscribed"}
