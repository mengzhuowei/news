"""Telegram 订阅 API — 创建/确认/管理 Telegram 推送订阅"""

import secrets
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from web.database import get_db
from web.models import Subscription
from web.telegram_service import TelegramService
from web.routes.auth import verify_token

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


def _get_telegram_service() -> TelegramService:
    """懒加载 TelegramService 实例"""
    if not hasattr(_get_telegram_service, '_instance'):
        _get_telegram_service._instance = TelegramService()
    return _get_telegram_service._instance


class TelegramSubscribeRequest(BaseModel):
    chat_id: int
    categories: List[str] = []
    push_time: str = "09:00:00"
    timezone: str = "UTC"

    @field_validator('push_time')
    @classmethod
    def validate_push_time(cls, v):
        if len(v) != 8 or v[2] != ':' or v[5] != ':':
            raise ValueError('push_time must be in HH:MM:SS format')
        try:
            hour, minute, second = map(int, v.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
                raise ValueError
        except ValueError:
            raise ValueError('push_time must be valid time (HH:MM:SS)')
        return v


class TelegramUpdateRequest(BaseModel):
    push_time: Optional[str] = None
    timezone: Optional[str] = None
    categories: Optional[List[str]] = None
    frequency: Optional[str] = None

    @field_validator('push_time')
    @classmethod
    def validate_push_time(cls, v):
        if v is not None:
            if len(v) != 8 or v[2] != ':' or v[5] != ':':
                raise ValueError('push_time must be in HH:MM:SS format')
            try:
                hour, minute, second = map(int, v.split(':'))
                if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
                    raise ValueError
            except ValueError:
                raise ValueError('push_time must be valid time (HH:MM:SS)')
        return v

    @field_validator('frequency')
    @classmethod
    def validate_frequency(cls, v):
        if v is not None and v not in ('daily', 'weekly'):
            raise ValueError('frequency must be daily or weekly')
        return v


@router.post("/subscribe")
async def subscribe(
    request: TelegramSubscribeRequest,
    db: Session = Depends(get_db),
):
    """创建 Telegram 订阅"""
    # 检查是否已存在该 chat_id 的订阅
    existing = db.query(Subscription).filter(
        Subscription.chat_id == request.chat_id
    ).first()

    if existing:
        if existing.is_active:
            # 已订阅，返回通用消息
            return {"message": "Subscription already exists."}
        else:
            # 之前取消的订阅，重新激活
            existing.categories = request.categories
            existing.push_time = request.push_time
            existing.timezone = request.timezone
            existing.is_active = True
            db.commit()
            return {"message": "Subscription reactivated.", "chat_id": request.chat_id}

    # 创建新订阅
    subscription = Subscription(
        chat_id=request.chat_id,
        categories=request.categories,
        push_time=request.push_time,
        timezone=request.timezone,
        push_channel="telegram",
        is_active=True,
        confirm_token=secrets.token_urlsafe(32),
        confirmed_at=datetime.now(timezone.utc),
    )
    db.add(subscription)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"message": "Subscription already exists."}
    db.refresh(subscription)

    # 发送欢迎消息
    service = _get_telegram_service()
    await service.send_welcome(request.chat_id, request.categories)

    return {
        "message": "Subscription created successfully.",
        "chat_id": subscription.chat_id,
        "categories": subscription.categories,
        "push_time": subscription.push_time,
    }


@router.get("/subscriptions/{chat_id}")
async def get_subscription(
    chat_id: int,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """查询 Telegram 订阅详情"""
    subscription = db.query(Subscription).filter(
        Subscription.chat_id == chat_id
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    verify_token(subscription, token)

    return {
        "chat_id": subscription.chat_id,
        "is_active": subscription.is_active,
        "frequency": subscription.frequency,
        "push_time": subscription.push_time,
        "timezone": subscription.timezone,
        "categories": subscription.categories or [],
        "confirmed_at": subscription.confirmed_at.isoformat() if subscription.confirmed_at else None,
        "last_pushed_at": subscription.last_pushed_at.isoformat() if subscription.last_pushed_at else None,
    }


@router.patch("/subscriptions/{chat_id}")
async def update_subscription(
    chat_id: int,
    request: TelegramUpdateRequest,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """更新 Telegram 订阅偏好"""
    subscription = db.query(Subscription).filter(
        Subscription.chat_id == chat_id
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    verify_token(subscription, token)

    if request.push_time is not None:
        subscription.push_time = request.push_time
    if request.timezone is not None:
        subscription.timezone = request.timezone
    if request.categories is not None:
        subscription.categories = request.categories
    if request.frequency is not None:
        subscription.frequency = request.frequency

    db.commit()
    return {"status": "updated"}


@router.delete("/subscriptions/{chat_id}")
async def unsubscribe(
    chat_id: int,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """取消 Telegram 订阅"""
    subscription = db.query(Subscription).filter(
        Subscription.chat_id == chat_id
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    verify_token(subscription, token)

    subscription.is_active = False
    db.commit()

    # 发送取消确认消息
    service = _get_telegram_service()
    await service.send_unsubscribe_confirm(chat_id)

    return {"status": "unsubscribed"}


@router.get("/confirm/{token}")
async def confirm_subscription(
    token: str,
    db: Session = Depends(get_db),
):
    """通过 URL 确认 Telegram 订阅（用于网页跳转确认）"""
    subscription = db.query(Subscription).filter(
        Subscription.confirm_token == token
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Invalid token")

    if subscription.confirmed_at and subscription.is_active:
        return {"status": "already_confirmed", "chat_id": subscription.chat_id}

    # 激活订阅
    subscription.confirmed_at = datetime.now(timezone.utc)
    subscription.is_active = True
    db.commit()

    # 发送欢迎消息
    service = _get_telegram_service()
    if subscription.chat_id:
        await service.send_welcome(subscription.chat_id, subscription.categories or [])

    return {"status": "confirmed", "chat_id": subscription.chat_id}
