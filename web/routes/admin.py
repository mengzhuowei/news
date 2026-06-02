"""管理 API — 文章管理/统计/日志/构建（需 Session Cookie 认证）

注意：Session 存储在进程内存中，多 worker 部署时需配置粘性会话或改用 Redis 等共享存储。
"""

import os
import hmac
import secrets
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session
from web.database import get_db
from web.models import Article, Subscription, FetchLog, EmailLog

router = APIRouter(prefix="/api/admin", tags=["admin"])

_ADMIN_SESSIONS: dict[str, float] = {}
_SESSION_TTL = 86400
_SESSION_COOKIE_NAME = "admin_session"
_SESSION_LOCK = threading.Lock()
_LAST_CLEANUP = 0.0
_CLEANUP_INTERVAL = 300.0


def _cleanup_expired_sessions():
    now = time.time()
    expired = [k for k, v in _ADMIN_SESSIONS.items() if now - v > _SESSION_TTL]
    for k in expired:
        del _ADMIN_SESSIONS[k]


def _maybe_cleanup():
    """每隔 5 分钟清理过期 session，避免频繁遍历"""
    global _LAST_CLEANUP
    now = time.time()
    if now - _LAST_CLEANUP < _CLEANUP_INTERVAL:
        return
    _LAST_CLEANUP = now
    _cleanup_expired_sessions()


def _verify_admin_credentials(username: str, password: str) -> bool:
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if not admin_password:
        return False
    return hmac.compare_digest(username, admin_username) and hmac.compare_digest(password, admin_password)


def verify_session(request: Request) -> str:
    """Session Cookie 验证 — 替代 HTTP Basic Auth，避免凭据存储在 localStorage"""
    session_id = request.cookies.get(_SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    with _SESSION_LOCK:
        _maybe_cleanup()
        if session_id not in _ADMIN_SESSIONS:
            raise HTTPException(status_code=401, detail="Not authenticated")

        if time.time() - _ADMIN_SESSIONS[session_id] > _SESSION_TTL:
            _ADMIN_SESSIONS.pop(session_id, None)
            raise HTTPException(status_code=401, detail="Session expired")

        _ADMIN_SESSIONS[session_id] = time.time()

    return session_id


@router.post("/login")
async def admin_login(request: Request):
    """Admin 登录 — 验证凭据后设置 HttpOnly Session Cookie"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    username = body.get("username", "")
    password = body.get("password", "")

    if not _verify_admin_credentials(username, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = secrets.token_urlsafe(32)
    with _SESSION_LOCK:
        _cleanup_expired_sessions()
        _ADMIN_SESSIONS[session_id] = time.time()

    response = Response(
        content='{"status":"ok"}',
        media_type="application/json",
        status_code=200,
    )
    response.set_cookie(
        key=_SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="strict",
        max_age=_SESSION_TTL,
        secure=os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true",
    )
    return response


@router.post("/logout")
async def admin_logout(request: Request, response: Response):
    """Admin 登出 — 清除 Session Cookie"""
    session_id = request.cookies.get(_SESSION_COOKIE_NAME)
    if session_id:
        with _SESSION_LOCK:
            _ADMIN_SESSIONS.pop(session_id, None)

    response = Response(
        content='{"status":"ok"}',
        media_type="application/json",
        status_code=200,
    )
    response.delete_cookie(key=_SESSION_COOKIE_NAME)
    return response


@router.get("/stats")
async def get_stats(admin: str = Depends(verify_session), db: Session = Depends(get_db)):
    """站点统计（需认证）"""
    return {
        "total_articles": db.query(Article).count(),
        "total_subscriptions": db.query(Subscription).count(),
        "active_subscriptions": db.query(Subscription).filter(Subscription.is_active == True).count(),
        "rewritten_articles": db.query(Article).filter(Article.is_rewritten == True).count(),
        "unrewritten_articles": db.query(Article).filter(Article.is_rewritten == False).count(),
        "built_articles": db.query(Article).filter(Article.is_built == True).count(),
        "unbuilt_articles": db.query(Article).filter(Article.is_built == False).count(),
    }


@router.get("/articles")
async def list_articles(
    source: Optional[str] = None,
    is_built: Optional[bool] = None,
    is_rewritten: Optional[bool] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(verify_session),
    db: Session = Depends(get_db),
):
    """文章列表（支持筛选和分页，需认证）"""
    query = db.query(Article)
    if source:
        query = query.filter(Article.source == source)
    if is_built is not None:
        query = query.filter(Article.is_built == is_built)
    if is_rewritten is not None:
        query = query.filter(Article.is_rewritten == is_rewritten)

    total = query.count()
    articles = query.order_by(Article.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": a.id,
                "url": a.url,
                "title": a.title,
                "source": a.source,
                "category": a.category,
                "slug": a.slug,
                "is_rewritten": a.is_rewritten,
                "is_built": a.is_built,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in articles
        ],
    }


@router.get("/articles/sources")
async def get_article_sources(
    admin: str = Depends(verify_session),
    db: Session = Depends(get_db),
):
    """按来源统计文章数（需认证）

    注意：此端点必须在 /{article_id} 之前注册，否则 FastAPI 会将 sources 误匹配为路径参数。
    """
    results = db.query(Article.source, func.count(Article.id)).group_by(Article.source).all()
    return [{"source": r[0], "count": r[1]} for r in results]


@router.get("/articles/{article_id}")
async def get_article(
    article_id: int,
    admin: str = Depends(verify_session),
    db: Session = Depends(get_db),
):
    """文章详情（需认证）"""
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    return {
        "id": article.id,
        "url": article.url,
        "title": article.title,
        "source": article.source,
        "category": article.category,
        "slug": article.slug,
        "is_rewritten": article.is_rewritten,
        "is_built": article.is_built,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "created_at": article.created_at.isoformat() if article.created_at else None,
    }


@router.delete("/articles/{article_id}")
async def delete_article(
    article_id: int,
    admin: str = Depends(verify_session),
    db: Session = Depends(get_db),
):
    """删除文章（需认证）"""
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    db.delete(article)
    db.commit()
    return {"status": "deleted"}


@router.get("/fetch/logs")
async def get_fetch_logs(
    source: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(verify_session),
    db: Session = Depends(get_db),
):
    """抓取日志（分页，需认证）"""
    query = db.query(FetchLog)
    if source:
        query = query.filter(FetchLog.source == source)
    total = query.count()
    logs = query.order_by(FetchLog.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": log.id,
                "source": log.source,
                "articles_count": log.articles_count,
                "status": log.status,
                "error_message": log.error_message,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }


@router.get("/subscriptions")
async def list_subscriptions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(verify_session),
    db: Session = Depends(get_db),
):
    """订阅用户列表（分页，需认证）"""
    total = db.query(Subscription).count()
    subscriptions = (
        db.query(Subscription)
        .order_by(Subscription.created_at.desc())
        .offset(skip).limit(limit).all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": s.id,
                "email": s.email,
                "is_active": s.is_active,
                "frequency": s.frequency,
                "site": s.site,
                "categories": s.categories if s.categories else [],
                "confirmed_at": s.confirmed_at.isoformat() if s.confirmed_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in subscriptions
        ],
    }


@router.get("/email/logs")
async def get_email_logs(
    email_type: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(verify_session),
    db: Session = Depends(get_db),
):
    """邮件发送日志（分页，需认证）"""
    query = db.query(EmailLog)
    if email_type:
        query = query.filter(EmailLog.email_type == email_type)
    if status:
        query = query.filter(EmailLog.status == status)
    total = query.count()
    logs = query.order_by(EmailLog.sent_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": log.id,
                "subscription_id": log.subscription_id,
                "email_type": log.email_type,
                "status": log.status,
                "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            }
            for log in logs
        ],
    }


_build_status = {"status": "idle"}
_build_lock = threading.Lock()


@router.post("/build")
async def trigger_build(admin: str = Depends(verify_session)):
    """触发站点构建（后台线程，需认证）"""
    global _build_status
    started_at = datetime.now(timezone.utc).isoformat()
    with _build_lock:
        if _build_status.get("status") == "building":
            raise HTTPException(status_code=409, detail="构建正在进行中，请稍后再试")
        _build_status = {
            "status": "building",
            "started_at": started_at,
        }

    from fetcher.site_builder import SiteBuilder

    project_root = Path(__file__).parent.parent.parent
    output_dir = Path(os.getenv("DIST_DIR", str(project_root / "dist")))

    def _build():
        global _build_status
        try:
            builder = SiteBuilder(output_dir)
            success = builder.build_all()
            from web.database import get_session
            from web.models import Article as ArticleModel
            with get_session() as db:
                articles_count = db.query(ArticleModel).count()
            with _build_lock:
                _build_status = {
                    "status": "success" if success else "failed",
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "message": "站点构建成功" if success else "站点构建失败",
                    "articles_count": articles_count,
                }
        except Exception as e:
            with _build_lock:
                _build_status = {
                    "status": "failed",
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "message": str(e),
                }

    thread = threading.Thread(target=_build, daemon=True)
    thread.start()
    return {"status": "started", "message": "站点构建已启动"}


@router.get("/build/status")
async def get_build_status(admin: str = Depends(verify_session)):
    """查询构建状态（需认证）"""
    with _build_lock:
        return dict(_build_status)
