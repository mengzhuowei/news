"""Jinja2 静态站点构建器 — 从数据库或 Markdown 文件生成静态 HTML"""

import os
import re
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
TELEGRAM_BOT_USERNAME_DEFAULT = ""
ARTICLES_PER_PAGE = 20


class SiteBuilder:
    """Jinja2 静态站点构建器 — 从数据库或 Markdown 文件生成静态 HTML"""

    def _refresh_config(self):
        self.site_title = os.getenv("SITE_TITLE", SITE_TITLE_DEFAULT)
        self.site_baseurl = os.getenv("SITE_BASEURL", SITE_BASEURL_DEFAULT)
        self.adsense_client_id = os.getenv("ADSENSE_CLIENT_ID", ADSENSE_CLIENT_ID_DEFAULT)
        self.telegram_bot_username = os.getenv("TELEGRAM_BOT_USERNAME", TELEGRAM_BOT_USERNAME_DEFAULT)
        if not self.site_baseurl:
            logger.warning(
                "SITE_BASEURL is empty; RSS and Sitemap will contain relative URLs. "
                "Set SITE_BASEURL for production deployments."
            )

    def __init__(self, content_dir: str | Path, output_dir: str | Path,
                 max_workers: int | None = None, use_db: bool = True):
        self.content_dir = Path(content_dir)
        self.output_dir = Path(output_dir)
        self.max_workers = max_workers or min(16, (os.cpu_count() or 4) + 2)
        self.use_db = use_db
        self._refresh_config()
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(
                str(Path(__file__).parent.parent / "shared" / "templates" / "jinja2")
            ),
            autoescape=jinja2.select_autoescape(["html"]),
        )

    def _base_ctx(self, **extra) -> dict:
        ctx = {
            "site_title": self.site_title,
            "site_baseurl": self.site_baseurl,
            "adsense_client_id": self.adsense_client_id,
        }
        ctx.update(extra)
        return ctx

    def _load_from_db(self) -> dict[str, list[dict]]:
        """从数据库读取文章，按分类组织"""
        from web.database import SessionLocal
        from web.models import Article

        sections: dict[str, list[dict]] = {}
        db = SessionLocal()
        try:
            articles = db.query(Article).order_by(Article.published_at.desc()).all()
            for a in articles:
                category = a.category or "uncategorized"
                slug = a.slug or f"article-{a.id}"
                date = a.published_at or a.created_at or datetime.now(timezone.utc)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)

                sections.setdefault(category, []).append({
                    "id": a.id,
                    "title": a.title or "Untitled",
                    "summary": a.summary or "",
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
                    "published_iso": date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "published_display": (
                        f"{calendar.month_name[date.month]} {date.day}, {date.year}"
                    ),
                    "permalink": f"{self.site_baseurl}/{category}/{slug}/",
                    "slug": slug,
                    "section": category,
                    "rewritten": a.is_rewritten or False,
                })

            for cat in sections:
                sections[cat].sort(key=lambda a: a["date"], reverse=True)
        finally:
            db.close()
        return sections

    def _load_from_files(self) -> dict[str, list[dict]]:
        """从 Markdown 文件读取文章（兼容旧数据）"""
        sections: dict[str, list[dict]] = {}
        if not self.content_dir.exists():
            return sections

        for category_dir in self.content_dir.iterdir():
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            for md_file in sorted(category_dir.glob("*.md"), reverse=True):
                text = md_file.read_text(encoding="utf-8")
                title_match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
                title = title_match.group(1) if title_match else md_file.stem
                slug = md_file.stem
                date = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)

                sections.setdefault(category, []).append({
                    "title": title,
                    "summary": "",
                    "content": text,
                    "content_html": markdown.markdown(
                        text, extensions=["tables", "fenced_code"]
                    ),
                    "category": category,
                    "source": "",
                    "source_url": "",
                    "author": "Unknown",
                    "image": "",
                    "date": date,
                    "published_iso": date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "published_display": (
                        f"{calendar.month_name[date.month]} {date.day}, {date.year}"
                    ),
                    "permalink": f"{self.site_baseurl}/{category}/{slug}/",
                    "slug": slug,
                    "section": category,
                    "rewritten": False,
                })

        return sections

    def build_all(self) -> bool:
        try:
            self._refresh_config()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            sections = self._load_from_db() if self.use_db else self._load_from_files()

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
            self.build_rss(sections)
            self.build_robots_txt()

            if self.use_db:
                self._mark_articles_as_built(sections)

            return True
        except Exception as e:
            logger.error(f"Build failed: {e}")
            return False

    def _mark_articles_as_built(self, sections: dict[str, list[dict]]):
        """标记所有文章为已构建 — 使用 ID 而非 slug 防止碰撞误更新"""
        from web.database import SessionLocal
        from web.models import Article

        db = SessionLocal()
        try:
            article_ids = []
            for articles in sections.values():
                for article in articles:
                    if article.get("id"):
                        article_ids.append(article["id"])

            if article_ids:
                db.query(Article).filter(Article.id.in_(article_ids)).update(
                    {"is_built": True}, synchronize_session=False
                )
                db.commit()
                logger.info(f"Marked {len(article_ids)} articles as built")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to mark articles as built: {e}")
        finally:
            db.close()

    def _render_article_page(self, section_name: str, article: dict, sections: dict):
        """渲染单篇文章页"""
        template = self.env.get_template("single.html")
        out_dir = self.output_dir / section_name / article["slug"]
        out_dir.mkdir(parents=True, exist_ok=True)

        ctx = self._base_ctx(
            page=article,
            title=article["title"],
            permalink=article["permalink"],
            canonical_url=article["permalink"],
            page_lang=article.get("language", "en"),
            sections={k: {"count": len(v)} for k, v in sections.items()},
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

        canonical_url = f"{self.site_baseurl}/{section_name}/"
        if page_num > 1:
            canonical_url = f"{self.site_baseurl}/{section_name}/page/{page_num}/"

        prev_url = None
        if page_num > 1:
            prev_url = f"/{section_name}/" if page_num == 2 else f"/{section_name}/page/{page_num - 1}/"

        next_url = None
        if page_num < total_pages:
            next_url = f"/{section_name}/page/{page_num + 1}/"

        ctx = self._base_ctx(
            section_name=section_name,
            articles=page_articles,
            page=page_num,
            total_pages=total_pages,
            has_prev=page_num > 1,
            has_next=page_num < total_pages,
            canonical_url=canonical_url,
            prev_url=prev_url,
            next_url=next_url,
            sections={k: {"count": len(v)} for k, v in sections.items()},
        )
        (out_dir / "index.html").write_text(template.render(**ctx), encoding="utf-8")

    def build_homepage(self, sections: dict):
        """构建首页"""
        all_articles = sorted(
            [a for arts in sections.values() for a in arts],
            key=lambda a: a["date"], reverse=True,
        )
        template = self.env.get_template("index.html")
        ctx = self._base_ctx(
            telegram_bot_username=self.telegram_bot_username,
            hero_article=all_articles[0] if all_articles else None,
            hero_side_articles=all_articles[1:4],
            latest_articles=all_articles[4:10],
            canonical_url=f"{self.site_baseurl}/",
            sections={k: {"count": len(v)} for k, v in sections.items()},
        )
        (self.output_dir / "index.html").write_text(
            template.render(**ctx), encoding="utf-8"
        )

    def build_sitemap(self, sections: dict):
        """生成 sitemap.xml — 包含首页、分类页和文章页"""
        urlset = Element("urlset")
        urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        home_el = SubElement(urlset, "url")
        SubElement(home_el, "loc").text = f"{self.site_baseurl}/"
        SubElement(home_el, "lastmod").text = now_iso
        SubElement(home_el, "changefreq").text = "hourly"
        SubElement(home_el, "priority").text = "1.0"

        for section_name, articles in sections.items():
            cat_el = SubElement(urlset, "url")
            SubElement(cat_el, "loc").text = f"{self.site_baseurl}/{section_name}/"
            SubElement(cat_el, "lastmod").text = articles[0]["published_iso"] if articles else now_iso
            SubElement(cat_el, "changefreq").text = "hourly"
            SubElement(cat_el, "priority").text = "0.8"

            for article in articles:
                url_el = SubElement(urlset, "url")
                SubElement(url_el, "loc").text = article["permalink"]
                SubElement(url_el, "lastmod").text = article["published_iso"]
                SubElement(url_el, "changefreq").text = "daily"
                SubElement(url_el, "priority").text = "0.6"

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
            urlset, encoding="unicode"
        )
        (self.output_dir / "sitemap.xml").write_text(xml_str, encoding="utf-8")

    def build_rss(self, sections: dict):
        """生成 RSS feed"""
        all_articles = sorted(
            [a for arts in sections.values() for a in arts],
            key=lambda a: a["date"], reverse=True,
        )[:50]

        from email.utils import format_datetime

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
            f'  <link>{_escape_xml(self.site_baseurl)}/</link>\n'
            f'  <description>{_escape_xml(self.site_title)} - News Aggregator</description>\n'
            f'{chr(10).join(items)}\n'
            '</channel>\n</rss>'
        )
        (self.output_dir / "index.xml").write_text(rss, encoding="utf-8")

    def build_robots_txt(self):
        """生成 robots.txt"""
        sitemap_url = f"{self.site_baseurl}/sitemap.xml" if self.site_baseurl else "/sitemap.xml"
        content = (
            "User-agent: *\n"
            "Allow: /\n"
            f"Sitemap: {sitemap_url}\n"
        )
        (self.output_dir / "robots.txt").write_text(content, encoding="utf-8")


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
