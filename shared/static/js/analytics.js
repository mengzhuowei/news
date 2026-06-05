/* News Aggregator — GA4 关键事件追踪
 * 依赖：base.html 已加载 gtag({{ ga_id }}) 全局函数
 * 事件列表（建议在 GA4 后台全部标记为 Key Event）：
 *   - article_view           单篇文章页面加载
 *   - article_scroll_50      阅读深度 50%
 *   - article_scroll_100     阅读深度 100%
 *   - homepage_card_click    首页文章卡片点击
 *   - outbound_click         站外链接点击（含 Original Source / Telegram Bot）
 *   - newsletter_signup_click 订阅按钮点击
 *   - search                 站内搜索提交
 *   - pagination_click       列表分页点击
 */
(function () {
  "use strict";
  if (typeof window.gtag !== "function") return;

  var body = document.body;
  if (!body) return;

  var pageType = body.getAttribute("data-page-type") || "";
  var articleId = body.getAttribute("data-article-id") || "";
  var articleTitle = body.getAttribute("data-article-title") || "";
  var articleSource = body.getAttribute("data-article-source") || "";
  var articleCategory = body.getAttribute("data-article-category") || "";
  var sectionName = body.getAttribute("data-section") || "";

  // ---------- 单篇文章 ----------
  if (pageType === "article") {
    gtag("event", "article_view", {
      article_id: articleId,
      article_title: articleTitle,
      article_source: articleSource,
      article_category: articleCategory,
    });

    var sent = { 50: false, 100: false };
    function onScroll() {
      var doc = document.documentElement;
      var max = doc.scrollHeight - window.innerHeight;
      if (max <= 0) return;
      var pct = Math.round((window.scrollY / max) * 100);
      if (pct >= 50 && !sent[50]) {
        sent[50] = true;
        gtag("event", "article_scroll_50", {
          article_id: articleId,
          article_title: articleTitle,
        });
      }
      if (pct >= 100 && !sent[100]) {
        sent[100] = true;
        gtag("event", "article_scroll_100", {
          article_id: articleId,
          article_title: articleTitle,
        });
        window.removeEventListener("scroll", onScroll);
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    // 极短文章立即触发 100%
    window.requestAnimationFrame(onScroll);
  }

  // ---------- 首页 / 列表页：文章卡片点击 ----------
  if (pageType === "home" || pageType === "list") {
    document.addEventListener("click", function (e) {
      var card = e.target.closest(".article-card, .hero");
      if (!card) return;
      var link = e.target.closest("a[href]");
      if (!link) return;
      var titleEl = card.querySelector("h2");
      gtag("event", "homepage_card_click", {
        click_url: link.href,
        click_title: titleEl ? titleEl.textContent.trim() : "",
        click_section: sectionName,
      });
    });
  }

  // ---------- 站外链接（Original Source / Telegram Bot / 外部 RSS 等） ----------
  document.addEventListener("click", function (e) {
    var link = e.target.closest('a[href^="http"]');
    if (!link) return;
    var href = link.getAttribute("href") || "";
    // 忽略站内链接与锚点
    try {
      var u = new URL(href, window.location.href);
      if (u.hostname === window.location.hostname) return;
    } catch (_) {
      return;
    }
    // 跳过已经在 data-ga-skip 标记的链接（如 RSS 自动发现）
    if (link.hasAttribute("data-ga-skip")) return;
    gtag("event", "outbound_click", {
      link_url: href,
      link_domain: link.hostname || "",
      link_text: (link.textContent || "").trim().slice(0, 80),
    });
  });

  // ---------- 订阅按钮（多处复用） ----------
  document.addEventListener("click", function (e) {
    var btn = e.target.closest('[data-ga-event="newsletter_signup_click"]');
    if (!btn) return;
    gtag("event", "newsletter_signup_click", {
      source: btn.getAttribute("data-ga-source") || "unknown",
      destination: btn.getAttribute("href") || "",
    });
  });

  // ---------- 站内搜索 ----------
  var searchForm = document.querySelector('form[action*="/search/"]');
  if (searchForm) {
    searchForm.addEventListener("submit", function () {
      var input = searchForm.querySelector('input[name="q"]');
      var term = input ? (input.value || "").trim() : "";
      gtag("event", "search", { search_term: term });
    });
  }

  // ---------- 列表分页 ----------
  document.addEventListener("click", function (e) {
    var a = e.target.closest(".pagination a");
    if (!a) return;
    gtag("event", "pagination_click", {
      click_url: a.getAttribute("href") || "",
      section: sectionName,
    });
  });
})();
