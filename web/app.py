"""FastAPI 主应用 — 静态站点 + API 服务"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from web.database import init_db
from web.routes import subscribe, subscription, admin, telegram_subscribe


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("[app] Initializing database...")
    init_db()
    logger.info("[app] Database initialization complete")
    yield


app = FastAPI(title="News Aggregator API", lifespan=lifespan)

# CORS: 当 allow_credentials=True 时，allow_origins 不能用 "*"，否则浏览器会拒绝
_cors_origins = os.getenv("CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins if o.strip() and o.strip() != "*"]
_allow_creds = True
if not _cors_origins:
    _raw = os.getenv("CORS_ORIGINS", "").strip()
    if _raw == "*":
        _cors_origins = ["*"]
        _allow_creds = False
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=_allow_creds,
        allow_methods=["*"],
        allow_headers=["*"],
    )


app.include_router(subscribe.router)
app.include_router(subscription.router)
app.include_router(admin.router)
app.include_router(telegram_subscribe.router)

DIST_DIR = Path(os.getenv("DIST_DIR", str(Path(__file__).parent.parent / "dist")))


@app.get("/", response_class=HTMLResponse)
async def root():
    """首页 — 返回静态站点 HTML"""
    main_index = DIST_DIR / "index.html"
    if main_index.exists():
        return HTMLResponse(content=main_index.read_text(encoding="utf-8"))
    return JSONResponse(content={"message": "News Aggregator API", "version": "1.0.0"})


ADMIN_DIR = Path(__file__).parent.parent / "admin"


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """管理后台页面"""
    admin_index = ADMIN_DIR / "index.html"
    if admin_index.exists():
        return HTMLResponse(content=admin_index.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Admin page not found")


TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")


@app.get("/subscribe", response_class=HTMLResponse)
async def subscribe_page():
    """订阅页面 — 引导用户通过 Telegram Bot 订阅"""
    bot_username = TELEGRAM_BOT_USERNAME
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Subscribe - News Aggregator</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8f9fa; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 2rem; }}
        .card {{ background: white; border-radius: 12px; padding: 2.5rem; box-shadow: 0 2px 12px rgba(0,0,0,0.1); text-align: center; margin-top: 3rem; }}
        h1 {{ font-size: 1.8rem; margin-bottom: 1rem; color: #1a1a2e; }}
        .icon {{ font-size: 4rem; margin-bottom: 1rem; }}
        .description {{ color: #666; margin-bottom: 2rem; line-height: 1.8; }}
        .steps {{ text-align: left; margin: 2rem 0; }}
        .step {{ display: flex; align-items: flex-start; gap: 1rem; margin-bottom: 1.5rem; }}
        .step-num {{ background: #e94560; color: white; width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; flex-shrink: 0; }}
        .step-text {{ padding-top: 4px; }}
        .step-text strong {{ color: #1a1a2e; }}
        .btn {{ display: inline-block; background: #e94560; color: white; padding: 0.9rem 2rem; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 1.1rem; transition: background 0.2s; }}
        .btn:hover {{ background: #d63851; }}
        .features {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 2rem 0; text-align: left; }}
        .feature {{ background: #f8f9fa; padding: 1rem; border-radius: 8px; }}
        .feature-icon {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
        .feature-text {{ font-size: 0.9rem; color: #555; }}
        footer {{ margin-top: 2rem; color: #999; font-size: 0.85rem; }}
        footer a {{ color: #e94560; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="icon">📬</div>
            <h1>Subscribe to News Digest</h1>
            <p class="description">Get curated news delivered to your Telegram every day.</p>

            <div class="features">
                <div class="feature">
                    <div class="feature-icon">📰</div>
                    <div class="feature-text">Daily & weekly digests from top sources</div>
                </div>
                <div class="feature">
                    <div class="feature-icon">🎯</div>
                    <div class="feature-text">Choose your preferred categories</div>
                </div>
                <div class="feature">
                    <div class="feature-icon">⏰</div>
                    <div class="feature-text">Customizable push time</div>
                </div>
                <div class="feature">
                    <div class="feature-icon">🆓</div>
                    <div class="feature-text">Completely free, no spam</div>
                </div>
            </div>

            <div class="steps">
                <div class="step">
                    <div class="step-num">1</div>
                    <div class="step-text"><strong>Click the button below</strong> to open our Telegram bot</div>
                </div>
                <div class="step">
                    <div class="step-num">2</div>
                    <div class="step-text"><strong>Send /start</strong> to begin your subscription</div>
                </div>
                <div class="step">
                    <div class="step-num">3</div>
                    <div class="step-text"><strong>Manage preferences</strong> with /categories and /settime</div>
                </div>
            </div>
"""

    if bot_username:
        html += f'            <a href="https://t.me/{bot_username}" class="btn" target="_blank">Open Telegram Bot</a>\n'
    else:
        html += '            <p style="color: #999;">Telegram bot not configured yet.</p>\n'

    html += """
            <footer>
                <p><a href="/">← Back to homepage</a></p>
            </footer>
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)


if DIST_DIR.exists():
    app.mount("/dist", StaticFiles(directory=str(DIST_DIR), html=True), name="dist")
