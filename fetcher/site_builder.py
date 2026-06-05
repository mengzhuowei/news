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
GA_ID_DEFAULT = ""
TWITTER_USERNAME_DEFAULT = ""
TWITTER_CREATOR_DEFAULT = ""
TELEGRAM_BOT_USERNAME_DEFAULT = ""
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
        self.ga_id = os.getenv("GA_ID", GA_ID_DEFAULT)
        self.telegram_bot_username = os.getenv("TELEGRAM_BOT_USERNAME", TELEGRAM_BOT_USERNAME_DEFAULT)
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
            "ga_id": self.ga_id,
            "telegram_bot_username": self.telegram_bot_username,
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
            self.build_rss(sections)
            self.build_robots_txt()

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
        """生成 sitemap.xml — 包含首页、分类页和文章页"""
        urlset = Element("urlset")
        urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")
        urlset.set("xmlns:image", "http://www.google.com/schemas/sitemap-image/1.1")

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        home_el = SubElement(urlset, "url")
        SubElement(home_el, "loc").text = f"{self._site_url}/"
        SubElement(home_el, "lastmod").text = now_iso
        SubElement(home_el, "changefreq").text = "hourly"
        SubElement(home_el, "priority").text = "1.0"

        for section_name, articles in sections.items():
            cat_el = SubElement(urlset, "url")
            SubElement(cat_el, "loc").text = f"{self._site_url}/{section_name}/"
            SubElement(cat_el, "lastmod").text = articles[0]["published_iso"] if articles else now_iso
            SubElement(cat_el, "changefreq").text = "hourly"
            SubElement(cat_el, "priority").text = "0.8"

            for article in articles:
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

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
            urlset, encoding="unicode"
        )
        (self.output_dir / "sitemap.xml").write_text(xml_str, encoding="utf-8")

    def build_news_sitemap(self, sections: dict):
        """生成 Google News 专用 sitemap（news-sitemap.xml）。
        仅收录 48 小时内的文章，避免被 Google News 拒收。
        参考：https://developers.google.com/search/docs/specialty/news
        """
        from datetime import timedelta

        urlset = Element("urlset")
        urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")
        urlset.set("xmlns:news", "https://www.google.com/schemas/sitemap-news/0.9")
        urlset.set("xmlns:image", "http://www.google.com/schemas/sitemap-image/1.1")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        total = 0
        for articles in sections.values():
            for article in articles:
                if article["date"] < cutoff:
                    continue
                url_el = SubElement(urlset, "url")
                # Google News 要求 loc 是文章页 URL，且与 sitemap.xml 一致
                SubElement(url_el, "loc").text = article["permalink"]

                news_el = SubElement(url_el, "news:news")
                pub_el = SubElement(news_el, "news:publication")
                # Google News 要求 publication name 固定且非空
                SubElement(pub_el, "news:name").text = self.site_title
                SubElement(pub_el, "news:language").text = (
                    article.get("language") or "en"
                )

                SubElement(news_el, "news:publication_date").text = article["published_iso"]
                SubElement(news_el, "news:title").text = article["title"]
                SubElement(news_el, "news:keywords").text = (
                    f"{article['category']}, {article['source']}"
                )
                if article.get("source_url"):
                    # 原来源（非必填，但有助于 Google 信任）
                    SubElement(news_el, "news:original_source").text = article["source"]

                if article.get("image"):
                    img_el = SubElement(url_el, "image:image")
                    SubElement(img_el, "image:loc").text = article["image"]
                    SubElement(img_el, "image:title").text = article["title"]

                total += 1
                # Google News sitemap 单文件上限 1000 条
                if total >= 1000:
                    break
            if total >= 1000:
                break

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
            urlset, encoding="unicode"
        )
        (self.output_dir / "news-sitemap.xml").write_text(xml_str, encoding="utf-8")
        logger.info(f"news-sitemap.xml generated ({total} articles)")

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
            '<rss version="2.0">\n<channel>\n'
            f'  <title>{_escape_xml(self.site_title)}</title>\n'
            f'  <link>{_escape_xml(self._site_url)}/</link>\n'
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
                '<rss version="2.0">\n<channel>\n'
                f'  <title>{_escape_xml(self.site_title)} - {section_name_display}</title>\n'
                f'  <link>{_escape_xml(self._site_url)}/{section_name}/</link>\n'
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
        seo_bots = [
            "AhrefsBot",
            "SemrushBot",
            "DotBot",
            "MJ12bot",
            "BLEXBot",
            "SeekportBot",
            "YandexBot",
            "YandexImages",
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
            "Crawl-Delay: 10",
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
            "",
        ]
        (self.output_dir / "robots.txt").write_text("\n".join(lines), encoding="utf-8")


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
