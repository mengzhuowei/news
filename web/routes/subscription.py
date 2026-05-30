"""订阅管理 API — 查询/更新订阅"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from web.database import get_db
from web.models import Subscription
from web.routes.auth import verify_token

router = APIRouter(prefix="/api/subscriptions", tags=["subscription-management"])


class SubscriptionUpdateRequest(BaseModel):
    frequency: Optional[str] = None
    categories: Optional[List[str]] = None

    @field_validator('frequency')
    @classmethod
    def validate_frequency(cls, v):
        if v is not None and v not in ('daily', 'weekly'):
            raise ValueError('frequency must be daily or weekly')
        return v


@router.get("/{email}")
async def get_subscription(
    email: str,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """查询订阅详情（需 token 或邮件验证）"""
    subscription = db.query(Subscription).filter(Subscription.email == email).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Not found")

    verify_token(subscription, token)

    return {
        "email": subscription.email,
        "is_active": subscription.is_active,
        "frequency": subscription.frequency,
        "site": subscription.site,
        "categories": subscription.categories if subscription.categories else [],
        "confirmed_at": subscription.confirmed_at.isoformat() if subscription.confirmed_at else None,
    }


@router.patch("/{email}")
async def update_subscription(
    email: str,
    request: SubscriptionUpdateRequest,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """更新订阅偏好（需 token 或邮件验证）"""
    subscription = db.query(Subscription).filter(Subscription.email == email).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Not found")

    verify_token(subscription, token)

    if request.frequency is not None:
        subscription.frequency = request.frequency
    if request.categories is not None:
        subscription.categories = request.categories

    db.commit()
    return {"status": "updated"}
