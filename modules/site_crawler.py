import re
from datetime import datetime, timedelta
from html import unescape

import config
from modules import db
from modules.wordpress_client import WordPressClient


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return unescape(text).strip()


def extract_keywords_from_title(title: str) -> str:
    stopwords = {"a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or", "is", "are", "your", "our", "how", "what", "why", "when"}
    words = re.findall(r"[a-z]+", title.lower())
    keywords = [w for w in words if w not in stopwords and len(w) > 2]
    return ", ".join(keywords[:8])


def crawl_site(wp: WordPressClient, force: bool = False) -> list[dict]:
    """Crawl eudaimoniahomes.com via WP REST API and build internal link map."""

    last_updated = db.get_links_last_updated()
    if not force and last_updated:
        lu = datetime.fromisoformat(last_updated)
        if datetime.now() - lu < timedelta(days=config.SITE_CACHE_REFRESH_DAYS):
            print("Site cache is fresh, using cached links.")
            return db.get_internal_links()

    print("Crawling site for internal link map...")
    links = []

    # Crawl pages (pillar pages, location pages)
    pages = wp.get_pages()
    for p in pages:
        title = strip_html(p.get("title", {}).get("rendered", ""))
        url = p.get("link", "")
        slug = p.get("slug", "")

        link_type = "page"
        lower_title = title.lower()
        if any(kw in lower_title for kw in ["austin", "sober living austin"]):
            link_type = "austin_pillar"
        elif any(kw in lower_title for kw in ["sober living", "recovery home"]):
            link_type = "pillar"

        links.append({
            "url": url,
            "title": title,
            "link_type": link_type,
            "keywords": extract_keywords_from_title(title),
        })

    # Crawl posts (existing blog articles)
    posts = wp.get_posts()
    for p in posts:
        title = strip_html(p.get("title", {}).get("rendered", ""))
        url = p.get("link", "")

        link_type = "blog"
        lower_title = title.lower()
        if "austin" in lower_title:
            link_type = "austin_blog"

        links.append({
            "url": url,
            "title": title,
            "link_type": link_type,
            "keywords": extract_keywords_from_title(title),
        })

    db.save_internal_links(links)
    print(f"Crawled {len(links)} pages and posts.")
    return links


def get_austin_pillar_links(links: list[dict]) -> list[dict]:
    """Filter to only Austin pillar/location pages."""
    pillar_links = [l for l in links if l["link_type"] in ("austin_pillar", "pillar")]

    # Always include the configured pillar pages even if titles didn't match
    existing_urls = {l["url"].rstrip("/") for l in pillar_links}
    for path in config.AUSTIN_PILLAR_PAGES:
        full_url = f"{config.WP_SITE_URL}{path}".rstrip("/")
        if full_url not in existing_urls:
            pillar_links.append({
                "url": f"{config.WP_SITE_URL}{path}",
                "title": path.strip("/").replace("-", " ").title(),
                "link_type": "austin_pillar",
                "keywords": "austin, sober living",
            })

    return pillar_links


def get_related_posts(links: list[dict], topic_keywords: list[str], limit: int = 3) -> list[dict]:
    """Find existing blog posts related to given keywords for internal linking."""
    scored = []
    for link in links:
        if link["link_type"] not in ("blog", "austin_blog"):
            continue
        link_kws = set(link.get("keywords", "").lower().split(", "))
        overlap = len(link_kws & set(kw.lower() for kw in topic_keywords))
        if overlap > 0:
            scored.append((overlap, link))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s[1] for s in scored[:limit]]
