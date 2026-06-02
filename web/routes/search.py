"""搜索路由 — API + 页面渲染"""

import os
from pathlib import Path
from urllib.parse import quote_plus
from fastapi import APIRouter, Query, Depends
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from loguru import logger

from web.database import get_db
from web.models import Article

router = APIRouter(tags=["search"])

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "shared" / "templates" / "jinja2"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["urlencode"] = lambda s: quote_plus(s or "")

CATEGORY_DISPLAY_NAMES = {
    "breaking-news": "Breaking News",
    "world": "World",
    "uk": "UK",
    "technology": "Technology",
    "business": "Business",
    "health": "Health",
    "culture": "Culture",
    "science": "Science",
    "politics": "Politics",
    "education": "Education",
    "sport": "Sport",
    "finance": "Finance",
    "markets": "Markets",
    "ai": "AI",
    "startups": "Startups",
    "economics": "Economics",
    "wealth": "Wealth",
    "companies": "Companies",
    "united-states": "United States",
    "climate": "Climate",
    "us": "U.S.",
}

SITE_TITLE = os.getenv("SITE_TITLE", "Global News Digest")
SITE_BASEURL = os.getenv("SITE_BASEURL", "")


def _load_sections(db: Session) -> dict[str, dict]:
    results = db.query(Article.category, func.count(Article.id)).group_by(Article.category).all()
    sections = {}
    for category, count in results:
        name = category or "uncategorized"
        display_name = CATEGORY_DISPLAY_NAMES.get(name, name.capitalize())
        sections[name] = {"count": count, "display_name": display_name}
    return sections


def _site_url() -> str:
    return SITE_BASEURL or f"https://{SITE_TITLE.lower().replace(' ', '-')}.com"


@router.get("/api/search")
async def search_articles_api(
    q: str = Query(..., min_length=1, max_length=200, description="搜索关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """搜索文章 API — 返回 JSON 格式结果"""
    query = db.query(Article)
    fulltext_clause = text(
        "MATCH (title, summary, content) AGAINST (:keyword IN NATURAL LANGUAGE MODE)"
    )
    query = query.filter(fulltext_clause).params(keyword=q)
    total = query.count()
    articles = (
        query.order_by(Article.published_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "query": q,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": [
            {
                "id": a.id,
                "title": a.title,
                "summary": a.summary,
                "source": a.source,
                "category": a.category,
                "slug": a.slug,
                "image_url": a.image_url or "",
                "published_at": a.published_at.isoformat() if a.published_at else None,
            }
            for a in articles
        ],
    }


@router.get("/search/", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    """搜索页面 — 服务端渲染搜索结果的 HTML 页面"""
    sections = _load_sections(db)
    articles_data = []
    total = 0
    page_size = 20

    if q:
        fulltext_clause = text(
            "MATCH (title, summary, content) AGAINST (:keyword IN NATURAL LANGUAGE MODE)"
        )
        query = db.query(Article).filter(fulltext_clause).params(keyword=q)
        total = query.count()
        articles = (
            query.order_by(Article.published_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        for a in articles:
            category = a.category or "uncategorized"
            slug = a.slug or f"article-{a.id}"
            date = a.published_at or a.created_at
            date_path = date.strftime("%Y/%m/%d") if date else "unknown"
            articles_data.append({
                "id": a.id,
                "title": a.title or "Untitled",
                "summary": a.summary or "",
                "source": a.source or "",
                "category": category,
                "slug": slug,
                "image": a.image_url or "",
                "date_path": date_path,
                "published_iso": date.strftime("%Y-%m-%dT%H:%M:%SZ") if date else "",
                "published_display": date.strftime("%B %d, %Y") if date else "",
                "permalink": f"{_site_url()}/{category}/{date_path}/{slug}/",
            })

    total_pages = max(1, (total + page_size - 1) // page_size)

    ctx = {
        "request": request,
        "site_title": SITE_TITLE,
        "site_baseurl": SITE_BASEURL,
        "site_url": _site_url(),
        "sections": sections,
        "adsense_client_id": os.getenv("ADSENSE_CLIENT_ID", ""),
        "ga_id": os.getenv("GA_ID", ""),
        "telegram_bot_username": os.getenv("TELEGRAM_BOT_USERNAME", ""),
        "query": q,
        "articles": articles_data,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": f"/search/?q={q}&page={page - 1}" if page > 1 else None,
        "next_url": f"/search/?q={q}&page={page + 1}" if page < total_pages else None,
        "canonical_url": f"{_site_url()}/search/?q={q}" if q else f"{_site_url()}/search/",
    }
    return templates.TemplateResponse("search.html", ctx)
