"""Jinja2 静态站点构建器 — 从数据库生成静态 HTML"""

import os
import shutil
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import jinja2
import markdown
from loguru import logger

SITE_TITLE_DEFAULT = "Global News Digest"
SITE_BASEURL_DEFAULT = ""
ADSENSE_CLIENT_ID_DEFAULT = ""
ADSENSE_SLOT_TOP_DEFAULT = ""
ADSENSE_SLOT_BOTTOM_DEFAULT = ""
ADSENSE_SLOT_SIDEBAR_DEFAULT = ""
GA_ID_DEFAULT = ""
SWG_PRODUCT_ID_DEFAULT = ""
SWG_ACCESS_TYPE_DEFAULT = "openaccess"  # 拆分后默认开 access
TWITTER_USERNAME_DEFAULT = ""
TWITTER_CREATOR_DEFAULT = ""
TELEGRAM_BOT_USERNAME_DEFAULT = ""
CONTACT_EMAIL_DEFAULT = ""
COPYRIGHT_EMAIL_DEFAULT = ""
PARTNERSHIPS_EMAIL_DEFAULT = ""

# Email 简单校验：要求基本邮箱格式，避免把垃圾值写进 HTML
import re as _re_email
_EMAIL_RE = _re_email.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ARTICLES_PER_PAGE = 20

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


class SiteBuilder:
    """Jinja2 静态站点构建器 — 从数据库生成静态 HTML"""

    def _refresh_config(self):
        self.site_title = os.getenv("SITE_TITLE", SITE_TITLE_DEFAULT)
        self.site_baseurl = os.getenv("SITE_BASEURL", SITE_BASEURL_DEFAULT)
        self.adsense_client_id = os.getenv("ADSENSE_CLIENT_ID", ADSENSE_CLIENT_ID_DEFAULT)
        self.adsense_slot_top = os.getenv("ADSENSE_SLOT_TOP", ADSENSE_SLOT_TOP_DEFAULT)
        self.adsense_slot_bottom = os.getenv("ADSENSE_SLOT_BOTTOM", ADSENSE_SLOT_BOTTOM_DEFAULT)
        self.adsense_slot_sidebar = os.getenv("ADSENSE_SLOT_SIDEBAR", ADSENSE_SLOT_SIDEBAR_DEFAULT)
        self.ga_id = os.getenv("GA_ID", GA_ID_DEFAULT)
        self.swg_product_id = os.getenv("SWG_PRODUCT_ID", SWG_PRODUCT_ID_DEFAULT)
        self.swg_access_type = os.getenv("SWG_ACCESS_TYPE", SWG_ACCESS_TYPE_DEFAULT)
        # 校验：access_type 必须是白名单之一
        if self.swg_access_type not in {"openaccess", "subscription", "metered"}:
            logger.warning(
                f"SWG_ACCESS_TYPE={self.swg_access_type!r} is not in "
                "{openaccess, subscription, metered}, falling back to 'openaccess'"
            )
            self.swg_access_type = SWG_ACCESS_TYPE_DEFAULT
        self.telegram_bot_username = os.getenv("TELEGRAM_BOT_USERNAME", TELEGRAM_BOT_USERNAME_DEFAULT)
        # 三个联系邮箱：未配置时为空字符串（模板里会整段隐藏）
        raw_contact = os.getenv("CONTACT_EMAIL", CONTACT_EMAIL_DEFAULT)
        raw_copyright = os.getenv("COPYRIGHT_EMAIL", COPYRIGHT_EMAIL_DEFAULT)
        raw_partner = os.getenv("PARTNERSHIPS_EMAIL", PARTNERSHIPS_EMAIL_DEFAULT)
        # 简单格式校验：不合法的当成空（避免把垃圾值塞进 HTML）
        self.contact_email = raw_contact if _EMAIL_RE.match(raw_contact) else ""
        self.copyright_email = raw_copyright if _EMAIL_RE.match(raw_copyright) else ""
        self.partnerships_email = raw_partner if _EMAIL_RE.match(raw_partner) else ""
        if raw_contact and not self.contact_email:
            logger.warning(f"CONTACT_EMAIL={raw_contact!r} is not a valid email, ignoring")
        if raw_copyright and not self.copyright_email:
            logger.warning(f"COPYRIGHT_EMAIL={raw_copyright!r} is not a valid email, ignoring")
        if raw_partner and not self.partnerships_email:
            logger.warning(f"PARTNERSHIPS_EMAIL={raw_partner!r} is not a valid email, ignoring")
        self.twitter_site = os.getenv("TWITTER_SITE", TWITTER_USERNAME_DEFAULT)
        self.twitter_creator = os.getenv("TWITTER_CREATOR", TWITTER_CREATOR_DEFAULT)
        if not self.site_baseurl:
            logger.warning(
                "SITE_BASEURL is empty; RSS and Sitemap will contain relative URLs. "
                "Set SITE_BASEURL for production deployments."
            )

    def __init__(self, output_dir: str | Path,
                 max_workers: int | None = None):
        self.output_dir = Path(output_dir)
        self.max_workers = max_workers or min(16, (os.cpu_count() or 4) + 2)
        self._refresh_config()
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(
                str(Path(__file__).parent.parent / "shared" / "templates" / "jinja2")
            ),
            autoescape=jinja2.select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def _base_ctx(self, **extra) -> dict:
        site_url = self.site_baseurl or f"https://{self.site_title.lower().replace(' ', '-')}.com"
        ctx = {
            "site_title": self.site_title,
            "site_baseurl": self.site_baseurl,
            "site_url": site_url,
            "adsense_client_id": self.adsense_client_id,
            "ad_slot_top": self.adsense_slot_top,
            "ad_slot_bottom": self.adsense_slot_bottom,
            "ad_slot_sidebar": self.adsense_slot_sidebar,
            "ga_id": self.ga_id,
            "swg_product_id": self.swg_product_id,
            "swg_access_type": self.swg_access_type,
            "telegram_bot_username": self.telegram_bot_username,
            "contact_email": self.contact_email,
            "copyright_email": self.copyright_email,
            "partnerships_email": self.partnerships_email,
            "twitter_site": self.twitter_site,
            "twitter_creator": self.twitter_creator,
        }
        ctx.update(extra)
        return ctx

    @property
    def _site_url(self) -> str:
        return self.site_baseurl or f"https://{self.site_title.lower().replace(' ', '-')}.com"

    @staticmethod
    def _smart_truncate(text: str, max_chars: int = 160) -> str:
        """Truncate text at word boundary within max_chars for meta descriptions."""
        if not text:
            return ""
        cleaned = " ".join(text.split())
        if len(cleaned) <= max_chars:
            return cleaned
        cutoff = cleaned.rfind(" ", 0, max_chars - 3)
        if cutoff == -1:
            cutoff = max_chars
        return cleaned[:cutoff] + "..."

    def _load_from_db(self) -> dict[str, list[dict]]:
        """从数据库读取文章，按分类组织（分批加载 + 释放 identity map 避免内存溢出）"""
        from web.database import get_session
        from web.models import Article

        sections: dict[str, list[dict]] = {}
        batch_size = 1000
        offset = 0
        with get_session() as db:
            while True:
                articles = db.query(Article).order_by(Article.published_at.desc()).offset(offset).limit(batch_size).all()
                if not articles:
                    break
                for a in articles:
                    category = a.category or "uncategorized"
                    slug = a.slug or f"article-{a.id}"
                    date = a.published_at or a.created_at or datetime.now(timezone.utc)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)

                    date_path = date.strftime("%Y/%m/%d")
                    sections.setdefault(category, []).append({
                        "id": a.id,
                        "title": a.title or "Untitled",
                        "summary": a.summary or "",
                        "description": self._smart_truncate(a.summary or "", 160),
                        "content": a.content or "",
                        "content_html": markdown.markdown(
                            a.content, extensions=["tables", "fenced_code"]
                        ) if a.content else "",
                        "category": category,
                        "source": a.source or "",
                        "source_url": a.url or "",
                        "author": a.author or "Unknown",
                        "image": a.image_url or "",
                        "video_url": a.video_url or "",
                        "video_duration": getattr(a, 'video_duration', None) or "",
                        "date": date,
                        "date_path": date_path,
                        "published_iso": date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "published_display": (
                            f"{calendar.month_name[date.month]} {date.day}, {date.year}"
                        ),
                        "permalink": f"{self._site_url}/{category}/{date_path}/{slug}/",
                        "slug": slug,
                        "section": category,
                        "rewritten": a.is_rewritten or False,
                    })
                db.expunge_all()
                offset += batch_size

            for cat in sections:
                sections[cat].sort(key=lambda a: a["date"], reverse=True)

            # 调整导航分类顺序：top → breaking-news → 其余
            priority_order = ["top", "breaking-news"]
            ordered = {}
            for key in priority_order:
                if key in sections:
                    ordered[key] = sections[key]
            for key, val in sections.items():
                if key not in priority_order:
                    ordered[key] = val
            sections = ordered

        return sections

    @staticmethod
    def _all_categories_keywords(sections: dict) -> str:
        """Build a comma-separated keyword string from all active categories."""
        names = []
        for k in sections.keys():
            if k == "uncategorized":
                continue
            names.append(CATEGORY_DISPLAY_NAMES.get(k, k.capitalize()))
        return ", ".join(names)

    def build_all(self) -> bool:
        try:
            self._refresh_config()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._copy_static_assets()
            self._build_manifest_json()
            sections = self._load_from_db()

            if not sections:
                (self.output_dir / "index.html").write_text(
                    "<html><body><h1>No articles yet</h1></body></html>"
                )
                return True

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for section_name, articles in sections.items():
                    for article in articles:
                        futures.append(
                            executor.submit(
                                self._render_article_page, section_name, article, sections
                            )
                        )
                    total_pages = max(
                        1, (len(articles) + ARTICLES_PER_PAGE - 1) // ARTICLES_PER_PAGE
                    )
                    for page_num in range(1, total_pages + 1):
                        futures.append(
                            executor.submit(
                                self._render_section_page, section_name, articles,
                                page_num, total_pages, sections,
                            )
                        )

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Thread error: {e}")

            self.build_homepage(sections)
            self.build_sitemap(sections)
            self.build_news_sitemap(sections)
            self.build_video_sitemap(sections)
            self.build_rss(sections)
            self.build_robots_txt()
            self.build_legal_pages(sections)

            self._mark_articles_as_built(sections)

            return True
        except Exception as e:
            logger.error(f"Build failed: {e}")
            return False

    def _build_manifest_json(self):
        """Generate a basic manifest.json for PWA / better bookmarking."""
        import json
        manifest = {
            "name": self.site_title,
            "short_name": self.site_title,
            "start_url": "/",
            "display": "standalone",
            "background_color": "#1a1a2e",
            "theme_color": "#1a1a2e",
            "description": f"{self.site_title} - Curated news from BBC, Reuters, Bloomberg and more",
            "icons": [
                {
                    "src": "/logo.png",
                    "sizes": "192x192",
                    "type": "image/png"
                },
                {
                    "src": "/logo.png",
                    "sizes": "512x512",
                    "type": "image/png"
                }
            ]
        }
        (self.output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("manifest.json generated")

    def _copy_static_assets(self):
        """Copy shared/static/* into the dist root (logo.png, js/, css/, etc.)."""
        static_root = Path(__file__).parent.parent / "shared" / "static"
        if not static_root.exists():
            return
        copied = 0
        for src in static_root.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(static_root)
            dest = self.output_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
        if copied:
            logger.info(f"Copied {copied} static asset(s) → {self.output_dir}")

    def _mark_articles_as_built(self, sections: dict[str, list[dict]]):
        """标记所有文章为已构建 — 使用 ID 而非 slug 防止碰撞误更新"""
        from web.database import get_session
        from web.models import Article

        article_ids = []
        for articles in sections.values():
            for article in articles:
                if article.get("id"):
                    article_ids.append(article["id"])

        if not article_ids:
            return

        try:
            with get_session() as db:
                db.query(Article).filter(Article.id.in_(article_ids)).update(
                    {"is_built": True}, synchronize_session=False
                )
            logger.info(f"Marked {len(article_ids)} articles as built")
        except Exception as e:
            logger.error(f"Failed to mark articles as built: {e}")

    def _get_related_articles(self, article: dict, sections: dict, count: int = 6) -> list[dict]:
        same_category = sections.get(article["category"], [])
        related = [a for a in same_category if a["id"] != article["id"]]
        return related[:count]

    def _render_article_page(self, section_name: str, article: dict, sections: dict):
        """渲染单篇文章页"""
        template = self.env.get_template("single.html")
        out_dir = self.output_dir / section_name / article["date_path"] / article["slug"]
        out_dir.mkdir(parents=True, exist_ok=True)

        related_articles = self._get_related_articles(article, sections)

        # 低质量判定：wordCount 太短、空内容、AI 重写失败残留
        # 阈值与 news-sitemap 保持一致（MIN_WORDCOUNT = 150）
        content = article.get("content") or ""
        word_count = len(content.split())

        # LLM 拒绝话术检测：识别 "no article body to rewrite" 等
        # 这种情况 wordCount 可能超过 150 阈值，必须用关键词兜底
        low_quality_phrases = (
            "no original article body to rewrite",
            "no factual content, numbers, or data are available",
            "no content to rewrite",
            "no content was provided",
            "no article body",
            "the provided text contains only a title",
            "no original content to process",
        )
        content_lower = content.lower()
        is_llm_refusal = any(phrase in content_lower for phrase in low_quality_phrases)

        # 空内容检测：content 只有 Source 标注行（无正文）
        # 典型模式："---\n**Source:** https://...\n*Generated at: ...*"
        content_stripped = content.strip()
        is_source_only = (
            content_stripped.startswith("---") and "**Source:**" in content_stripped
        ) if content_stripped else True

        low_quality = word_count < 150 or is_llm_refusal or is_source_only

        ctx = self._base_ctx(
            page=article,
            page_description=article["description"],
            title=article["title"],
            permalink=article["permalink"],
            canonical_url=article["permalink"],
            page_lang=article.get("language", "en"),
            page_type="article",
            article_id=article.get("id", ""),
            article_title=article.get("title", ""),
            article_source=article.get("source", ""),
            article_category=article.get("category", ""),
            section_name=section_name,
            related_articles=related_articles,
            low_quality=low_quality,
            sections={k: {"count": len(v), "display_name": CATEGORY_DISPLAY_NAMES.get(k, k.capitalize())} for k, v in sections.items()},
        )
        (out_dir / "index.html").write_text(template.render(**ctx), encoding="utf-8")

    def _render_section_page(
        self, section_name: str, articles: list[dict],
        page_num: int, total_pages: int, sections: dict,
    ):
        """渲染分类列表页"""
        template = self.env.get_template("list.html")
        start = (page_num - 1) * ARTICLES_PER_PAGE
        end = start + ARTICLES_PER_PAGE
        page_articles = articles[start:end]

        if page_num == 1:
            out_dir = self.output_dir / section_name
        else:
            out_dir = self.output_dir / section_name / "page" / str(page_num)
        out_dir.mkdir(parents=True, exist_ok=True)

        canonical_url = f"{self._site_url}/{section_name}/"
        if page_num > 1:
            canonical_url = f"{self._site_url}/{section_name}/page/{page_num}/"

        prev_url = None
        if page_num > 1:
            prev_url = f"/{section_name}/" if page_num == 2 else f"/{section_name}/page/{page_num - 1}/"

        next_url = None
        if page_num < total_pages:
            next_url = f"/{section_name}/page/{page_num + 1}/"

        section_name_display = CATEGORY_DISPLAY_NAMES.get(section_name, section_name.capitalize())

        ctx = self._base_ctx(
            section_name=section_name,
            section_name_display=section_name_display,
            page_type="list",
            section_name_ctx=section_name,
            articles=page_articles,
            page=page_num,
            total_pages=total_pages,
            has_prev=page_num > 1,
            has_next=page_num < total_pages,
            canonical_url=canonical_url,
            prev_url=prev_url,
            next_url=next_url,
            section_image=articles[0]["image"] if articles and articles[0].get("image") else "",
            sections={k: {"count": len(v), "display_name": CATEGORY_DISPLAY_NAMES.get(k, k.capitalize())} for k, v in sections.items()},
        )
        (out_dir / "index.html").write_text(template.render(**ctx), encoding="utf-8")

    def build_homepage(self, sections: dict):
        """构建首页"""
        all_articles = sorted(
            [a for arts in sections.values() for a in arts],
            key=lambda a: a["date"], reverse=True,
        )

        has_image = [a for a in all_articles if a.get("image")]
        no_image = [a for a in all_articles if not a.get("image")]

        template = self.env.get_template("index.html")
        ctx = self._base_ctx(
            page_type="home",
            hero_article=has_image[0] if has_image else None,
            hero_side_articles=has_image[1:4] if len(has_image) > 1 else [],
            latest_articles=has_image[4:10] if len(has_image) > 4 else (has_image[1:] if len(has_image) > 1 else []),
            no_image_articles=no_image[:15],
            canonical_url=f"{self._site_url}/",
            site_categories_keywords=self._all_categories_keywords(sections),
            sections={k: {"count": len(v), "display_name": CATEGORY_DISPLAY_NAMES.get(k, k.capitalize())} for k, v in sections.items()},
        )
        (self.output_dir / "index.html").write_text(
            template.render(**ctx), encoding="utf-8"
        )

    def build_sitemap(self, sections: dict):
        """生成 sitemap index + 分片 sitemap — 每个分类一个子 sitemap，避免单文件过大"""
        MAX_URLS_PER_SITEMAP = 5000

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- 首页 + 法律页面放入一个 "pages" sitemap ---
        pages_urlset = Element("urlset")
        pages_urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")
        pages_urlset.set("xmlns:image", "http://www.google.com/schemas/sitemap-image/1.1")
        # /contact/ 是 Google 评估 E-E-A-T（信任度）的重要因素
        for loc, lastmod, changefreq, priority in [
            (f"{self._site_url}/", now_iso, "hourly", "1.0"),
            (f"{self._site_url}/terms/", now_iso, "monthly", "0.9"),
            (f"{self._site_url}/privacy/", now_iso, "monthly", "0.9"),
            (f"{self._site_url}/contact/", now_iso, "monthly", "0.7"),
        ]:
            el = SubElement(pages_urlset, "url")
            SubElement(el, "loc").text = loc
            SubElement(el, "lastmod").text = lastmod
            SubElement(el, "changefreq").text = changefreq
            SubElement(el, "priority").text = priority
        pages_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
            pages_urlset, encoding="unicode"
        )
        (self.output_dir / "sitemap-pages.xml").write_text(pages_xml, encoding="utf-8")

        # --- 每个分类生成子 sitemap（文章过多时按 MAX_URLS_PER_SITEMAP 分片） ---
        sitemap_files = ["sitemap-pages.xml"]
        for section_name, articles in sections.items():
            total = len(articles)
            num_parts = max(1, (total + MAX_URLS_PER_SITEMAP - 1) // MAX_URLS_PER_SITEMAP)

            for part_idx in range(num_parts):
                chunk = articles[part_idx * MAX_URLS_PER_SITEMAP:(part_idx + 1) * MAX_URLS_PER_SITEMAP]
                urlset = Element("urlset")
                urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")
                urlset.set("xmlns:image", "http://www.google.com/schemas/sitemap-image/1.1")

                # 分类首页
                if part_idx == 0:
                    cat_el = SubElement(urlset, "url")
                    SubElement(cat_el, "loc").text = f"{self._site_url}/{section_name}/"
                    SubElement(cat_el, "lastmod").text = articles[0]["published_iso"] if articles else now_iso
                    SubElement(cat_el, "changefreq").text = "hourly"
                    SubElement(cat_el, "priority").text = "0.8"

                for article in chunk:
                    url_el = SubElement(urlset, "url")
                    SubElement(url_el, "loc").text = article["permalink"]
                    SubElement(url_el, "lastmod").text = article["published_iso"]
                    SubElement(url_el, "changefreq").text = "daily"
                    SubElement(url_el, "priority").text = "0.6"
                    if article.get("image"):
                        img_el = SubElement(url_el, "image:image")
                        SubElement(img_el, "image:loc").text = article["image"]
                        if article.get("title"):
                            SubElement(img_el, "image:title").text = article["title"]

                suffix = f"-{part_idx + 1}" if num_parts > 1 else ""
                filename = f"sitemap-{section_name}{suffix}.xml"
                xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
                    urlset, encoding="unicode"
                )
                (self.output_dir / filename).write_text(xml_str, encoding="utf-8")
                sitemap_files.append(filename)

        # --- 生成 sitemap index ---
        sitemapindex = Element("sitemapindex")
        sitemapindex.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")
        for fname in sitemap_files:
            sm_el = SubElement(sitemapindex, "sitemap")
            SubElement(sm_el, "loc").text = f"{self._site_url}/{fname}"
            SubElement(sm_el, "lastmod").text = now_iso
        index_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
            sitemapindex, encoding="unicode"
        )
        (self.output_dir / "sitemap.xml").write_text(index_xml, encoding="utf-8")

    def build_news_sitemap(self, sections: dict):
        """生成 Google News 专用 sitemap（news-sitemap.xml）。
        仅收录 48 小时内的文章，避免被 Google News 拒收。
        同时过滤低质量内容（wordCount 太短），避免给整站质量评估拖后腿。
        使用字符串拼接而不是 ElementTree，避免 ET 在带前缀命名空间
        （如 news:news / image:image）上的序列化 bug。
        参考：https://developers.google.com/search/docs/specialty/news/sitemap-news
        """
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        # news-sitemap 质量阈值：正文太短的页面不应进入 Google News 索引
        # 阈值根据文章词数估算（按空白分词，与单篇文章 wordCount 一致）
        MIN_WORDCOUNT = 150
        total = 0
        skipped_low_quality = 0
        items: list[str] = []

        for articles in sections.values():
            for article in articles:
                if article["date"] < cutoff:
                    continue

                # 质量过滤：wordCount 太短的不收录到 news-sitemap
                content = article.get("content") or ""
                word_count = len(content.split())

                # LLM 拒绝话术检测：识别 "no article body to rewrite" 等
                # 即使 wordCount 超过 150，只要命中关键词也视为低质量
                llm_refusal_phrases = (
                    "no original article body to rewrite",
                    "no factual content, numbers, or data are available",
                    "no content to rewrite",
                    "no content was provided",
                    "no article body",
                    "the provided text contains only a title",
                    "no original content to process",
                )
                is_llm_refusal = any(p in content.lower() for p in llm_refusal_phrases)

                # 空内容检测：content 只有 Source 标注行（无正文）
                content_stripped = content.strip()
                is_source_only = (
                    content_stripped.startswith("---") and "**Source:**" in content_stripped
                ) if content_stripped else True

                if word_count < MIN_WORDCOUNT or is_llm_refusal or is_source_only:
                    skipped_low_quality += 1
                    continue

                # Google News 流量加 UTM，让 GA4 正确归因到 Organic 而非 Unassigned
                news_url = article["permalink"] + "?utm_source=google_news&utm_medium=referral&utm_campaign=google_news"
                parts = [
                    "  <url>",
                    f"    <loc>{_escape_xml(news_url)}</loc>",
                    "    <news:news>",
                    "      <news:publication>",
                    f"        <news:name>{_escape_xml(self.site_title)}</news:name>",
                    f"        <news:language>{_escape_xml(article.get('language') or 'en')}</news:language>",
                    "      </news:publication>",
                    f"      <news:publication_date>{_escape_xml(article['published_iso'])}</news:publication_date>",
                    f"      <news:title>{_escape_xml(article['title'])}</news:title>",
                    f"      <news:keywords>{_escape_xml(article['category'] + ', ' + article['source'])}</news:keywords>",
                    "    </news:news>",
                ]
                if article.get("image"):
                    parts += [
                        "    <image:image>",
                        f"      <image:loc>{_escape_xml(article['image'])}</image:loc>",
                        f"      <image:title>{_escape_xml(article['title'])}</image:title>",
                        "    </image:image>",
                    ]
                parts.append("  </url>")
                items.append("\n".join(parts))

                total += 1
                if total >= 1000:  # Google News sitemap 单文件上限
                    break
            if total >= 1000:
                break

        # 注意：xmlns 必须全部在根标签里、xmlns:news 单独成对，
        # 顺序也按 Google 习惯（default namespace 在前）
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
            '        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"\n'
            '        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
            + "\n".join(items) + (items and "\n" or "")
            + "</urlset>\n"
        )
        (self.output_dir / "news-sitemap.xml").write_text(body, encoding="utf-8")
        logger.info(
            f"news-sitemap.xml generated ({total} articles, "
            f"{skipped_low_quality} skipped for low quality)"
        )

    def build_video_sitemap(self, sections: dict):
        """生成 Google 视频 sitemap（video-sitemap.xml）。
        仅收录含 video_url 的文章，帮助 Google 在视频搜索结果中展示。
        参考：https://developers.google.com/search/docs/crawling-indexing/sitemaps/video-sitemap
        """
        items: list[str] = []
        total = 0

        for articles in sections.values():
            for article in articles:
                if not article.get("video_url"):
                    continue

                # 区分原生视频文件 vs 第三方嵌入：
                # content_loc = 视频文件直接 URL（.mp4/.webm/.ogg）
                # player_loc = 第三方播放器嵌入 URL（YouTube/Vimeo 等）
                # 两者不应同时填写，否则 Google 可能混淆导致视频不被索引
                video_url = article['video_url']
                is_native_video = any(video_url.lower().endswith(ext) for ext in ('.mp4', '.webm', '.ogg'))

                parts = [
                    "  <url>",
                    f"    <loc>{_escape_xml(article['permalink'])}</loc>",
                    "    <video:video>",
                    f"      <video:thumbnail_loc>{_escape_xml(article.get('image') or self._site_url + '/logo.png')}</video:thumbnail_loc>",
                    f"      <video:title><![CDATA[{article['title']}]]></video:title>",
                    f"      <video:description><![CDATA[{article.get('summary') or ''}]]></video:description>",
                ]
                if is_native_video:
                    parts.append(f"      <video:content_loc>{_escape_xml(video_url)}</video:content_loc>")
                else:
                    parts.append(f"      <video:player_loc>{_escape_xml(video_url)}</video:player_loc>")
                parts += [
                    f"      <video:publication_date>{_escape_xml(article['published_iso'])}</video:publication_date>",
                    "      <video:family_friendly>yes</video:family_friendly>",
                    f"      <video:live>no</video:live>",
                ]
                if article.get("video_duration"):
                    parts.append(f"      <video:duration>{article['video_duration']}</video:duration>")
                parts += [
                    "    </video:video>",
                    "  </url>",
                ]
                items.append("\n".join(parts))
                total += 1
                if total >= 50000:  # sitemap 单文件上限
                    break
            if total >= 50000:
                break

        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
            '        xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">\n'
            + "\n".join(items) + (items and "\n" or "")
            + "</urlset>\n"
        )
        (self.output_dir / "video-sitemap.xml").write_text(body, encoding="utf-8")
        logger.info(f"video-sitemap.xml generated ({total} video articles)")

    def build_rss(self, sections: dict):
        """生成 RSS feed（全局 + 按分类）"""
        from email.utils import format_datetime

        now_rfc2822 = format_datetime(datetime.now(timezone.utc))

        all_articles = sorted(
            [a for arts in sections.values() for a in arts],
            key=lambda a: a["date"], reverse=True,
        )[:50]

        items = []
        for a in all_articles:
            pub_date = format_datetime(a["date"])
            items.append(
                f'  <item>\n'
                f'    <title>{_escape_xml(a["title"])}</title>\n'
                f'    <link>{_escape_xml(a["permalink"])}</link>\n'
                f'    <description>{_escape_xml(a["summary"] or "")}</description>\n'
                f'    <pubDate>{pub_date}</pubDate>\n'
                f'  </item>'
            )

        rss = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
            f'  <title>{_escape_xml(self.site_title)}</title>\n'
            f'  <link>{_escape_xml(self._site_url)}/</link>\n'
            f'  <atom:link href="{_escape_xml(self._site_url)}/index.xml" rel="self" type="application/rss+xml"/>\n'
            f'  <description>{_escape_xml(self.site_title)} - News Aggregator</description>\n'
            f'  <lastBuildDate>{now_rfc2822}</lastBuildDate>\n'
            f'  <copyright>Copyright {datetime.now(timezone.utc).year} {_escape_xml(self.site_title)}</copyright>\n'
            f'{chr(10).join(items)}\n'
            '</channel>\n</rss>'
        )
        (self.output_dir / "index.xml").write_text(rss, encoding="utf-8")

        for section_name, articles in sections.items():
            cat_dir = self.output_dir / section_name
            cat_dir.mkdir(parents=True, exist_ok=True)
            cat_items = []
            for a in articles[:30]:
                pub_date = format_datetime(a["date"])
                cat_items.append(
                    f'  <item>\n'
                    f'    <title>{_escape_xml(a["title"])}</title>\n'
                    f'    <link>{_escape_xml(a["permalink"])}</link>\n'
                    f'    <description>{_escape_xml(a["summary"] or "")}</description>\n'
                    f'    <pubDate>{pub_date}</pubDate>\n'
                    f'  </item>'
                )
            section_name_display = CATEGORY_DISPLAY_NAMES.get(section_name, section_name.capitalize())
            cat_rss = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
                f'  <title>{_escape_xml(self.site_title)} - {section_name_display}</title>\n'
                f'  <link>{_escape_xml(self._site_url)}/{section_name}/</link>\n'
                f'  <atom:link href="{_escape_xml(self._site_url)}/{section_name}/index.xml" rel="self" type="application/rss+xml"/>\n'
                f'  <description>{_escape_xml(self.site_title)} - Latest {section_name_display} News</description>\n'
                f'  <lastBuildDate>{now_rfc2822}</lastBuildDate>\n'
                f'{chr(10).join(cat_items)}\n'
                '</channel>\n</rss>'
            )
            (cat_dir / "index.xml").write_text(cat_rss, encoding="utf-8")

    def build_robots_txt(self):
        """生成 robots.txt：常规爬虫放行，AI 训练爬虫与明显扫描器拒绝。
        注意：robots.txt 是"君子协定"，严格拦截请配合 Nginx/Caddy/WAF。
        """
        sitemap_url = f"{self._site_url}/sitemap.xml"
        news_sitemap_url = f"{self._site_url}/news-sitemap.xml"
        video_sitemap_url = f"{self._site_url}/video-sitemap.xml"

        # AI 训练爬虫与已知骚扰 UA 名单
        # 涵盖主流 LLM/AI Search/SEO 爬虫与无脑扫描器
        ai_bots = [
            "GPTBot",              # OpenAI 训练
            "ChatGPT-User",        # OpenAI Browse
            "OAI-SearchBot",       # OpenAI Search
            "ClaudeBot",           # Anthropic 训练
            "Claude-SearchBot",    # Anthropic Search
            "Claude-User",         # Anthropic Browse
            "Claude-Web",          # Anthropic Web
            "PerplexityBot",       # Perplexity 训练
            "Perplexity-User",     # Perplexity Browse
            "Google-Extended",     # Google Gemini 训练（不影响 Googlebot 索引）
            "Applebot-Extended",   # Apple Intelligence 训练
            "Bytespider",          # 字节跳动
            "CCBot",               # Common Crawl → 多家 LLM
            "Meta-ExternalAgent",  # Meta AI
            "Meta-ExternalFetcher",
            "Amazonbot",           # Amazon AI
            "cohere-ai",           # Cohere
            "cohere-training-data-crawler",
            "DuckAssistBot",       # DuckDuckGo AI
            "FacebookBot",         # Meta（旧）
            "Gemini-Deep-Research",
            "Kangaroo Bot",        # 一些 AI 聚合
            "MistralAI-User",
            "anthropic-ai",
            "img2dataset",         # LAION 等数据集
            "LAION-AI",
            "Omgilibot",
            "Omgili",
            "TimpiBot",
            "YouBot",              # You.com
        ]

        # 已知的 SEO 爬虫/恶意扫描器（与 GA 流量无价值）
        # 注意：YandexBot 已移除 —— Yandex 是俄罗斯/东欧主要搜索引擎，保留可获取全球流量
        seo_bots = [
            "AhrefsBot",
            "SemrushBot",
            "DotBot",
            "MJ12bot",
            "BLEXBot",
            "SeekportBot",
            "Mail.Ru_Bot",
        ]

        lines = []
        # 1) 通用规则（仅对未被下方显式列出的 UA 生效）
        lines += [
            "User-agent: *",
            "Allow: /",
            "Disallow: /page/",
            "Disallow: /admin/",
            "Disallow: /subscribe",
            "Disallow: /subscription/",
            "Disallow: /api/",
            "Disallow: /search/?",
            "Disallow: /*?q=",
            "Disallow: /*?page=",
            "",
        ]
        # 2) AI 训练爬虫：整站拒绝
        for bot in ai_bots:
            lines += [f"User-agent: {bot}", "Disallow: /", ""]
        # 3) 主动 SEO 爬虫：拒绝（聚合站不需要被这些工具深度爬取）
        for bot in seo_bots:
            lines += [f"User-agent: {bot}", "Disallow: /", ""]
        # 4) Sitemap 引用
        lines += [
            f"Sitemap: {sitemap_url}",
            f"Sitemap: {news_sitemap_url}",
            f"Sitemap: {video_sitemap_url}",
            "",
        ]
        (self.output_dir / "robots.txt").write_text("\n".join(lines), encoding="utf-8")

    def build_legal_pages(self, sections: dict):
        """生成静态支持页面：/terms/、/privacy/、/contact/
        改写为静态文件（不再走 FastAPI），原因：
        1. 与"纯静态站点"架构一致
        2. 可被 Nginx / Cloudflare 缓存
        3. 减少对 web 容器的依赖

        接收 sections 字典是为了 base.html 的分类导航显示真实文章数
        """
        # 实际构建日期注入模板，用于"Last updated"显示
        build_date = datetime.now(timezone.utc).strftime("%B %d, %Y")
        # 复用真实 sections（让 base.html 的分类导航显示正确数字）
        ctx = self._base_ctx(
            page_type="legal",
            build_date=build_date,
            sections={k: {"count": len(v), "display_name": CATEGORY_DISPLAY_NAMES.get(k, k.capitalize())} for k, v in sections.items()},
        )
        for slug, tpl_name in [
            ("terms", "terms.html"),
            ("privacy", "privacy.html"),
            ("contact", "contact.html"),
        ]:
            template = self.env.get_template(tpl_name)
            out_dir = self.output_dir / slug
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "index.html").write_text(
                template.render(**ctx), encoding="utf-8"
            )
        logger.info("Support pages generated: /terms/, /privacy/, /contact/")


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")

    project_root = Path(__file__).parent.parent
    output_dir = Path(os.getenv("DIST_DIR", str(project_root / "dist")))

    builder = SiteBuilder(output_dir)
    success = builder.build_all()
    if success:
        print("站点构建成功")
        sys.exit(0)
    else:
        print("站点构建失败", file=sys.stderr)
        sys.exit(1)
