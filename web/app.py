"""FastAPI 主应用 — 静态站点 + API 服务"""

import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from web.routes import subscribe, subscription, admin

app = FastAPI(title="News Aggregator API")

# CORS: 当 allow_credentials=True 时，allow_origins 不能用 "*"，否则浏览器会拒绝
_cors_origins = os.getenv("CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


app.include_router(subscribe.router)
app.include_router(subscription.router)
app.include_router(admin.router)

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


if DIST_DIR.exists():
    app.mount("/dist", StaticFiles(directory=str(DIST_DIR), html=True), name="dist")
