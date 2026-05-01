#!/usr/bin/env python3
"""
Eudaimonia Blog Agent — Automated Pipeline Runner

Runs the full pipeline for all active sites:
  1. Fetch RingCentral calls → transcribe via RC AI API
  2. Route calls to sites by phone number
  3. Extract questions via Claude
  4. Check for duplicate topics on WordPress
  5. Generate full blog posts via Claude (with Yoast SEO fields)
  6. Publish to WordPress as scheduled future posts

Usage:
  python pipeline_runner.py                      # Full run, all sites
  python pipeline_runner.py --site eudaimonia    # Single site only
  python pipeline_runner.py --max-posts 1        # Cap posts per site
  python pipeline_runner.py --dry-run            # Print what would happen, don't publish
  python pipeline_runner.py --skip-calls         # Skip RC call fetching, only process pending questions
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import requests
from dotenv import load_dotenv

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env", override=True)

from modules import db
from modules.ringcentral_client import RingCentralClient
from modules.transcript_analyzer import analyze_transcript
from modules.site_config import (
    SiteConfig,
    get_all_sites,
    get_site,
    get_site_by_phone,
    wp_session_for_site,
)
try:
    from modules.wp_mysql_bridge import WPMySQLBridge
except ImportError:
    WPMySQLBridge = None  # pymysql not available — REST API only

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_SITE = "eudaimonia"
MAX_POSTS_PER_SITE = 2
RC_CALL_LOG_DAYS = 7

BLOG_PROMPT_PATH = PROJECT_ROOT / "prompts" / "write_blog_full.txt"
DB_PATH = PROJECT_ROOT / "db" / "tracker.db"

# Yoast green-light ranges
YOAST_TITLE_MIN = 30
YOAST_TITLE_MAX = 60
YOAST_DESC_MIN = 120
YOAST_DESC_MAX = 156


# ---------------------------------------------------------------------------
# MySQL bridge helpers (for sites where HTTP to own domain hangs)
# ---------------------------------------------------------------------------

_mysql_bridges: dict = {}


def _use_mysql(cfg: SiteConfig) -> bool:
    """Check if this site should use the direct MySQL bridge."""
    return cfg.wp_app_password.startswith("mysql:")


def _get_mysql_bridge(cfg: SiteConfig) -> WPMySQLBridge:
    """Get or create a MySQL bridge for a site."""
    if cfg.id not in _mysql_bridges:
        wp_path = cfg.wp_app_password.split(":", 1)[1]
        _mysql_bridges[cfg.id] = WPMySQLBridge(wp_path=wp_path)
    return _mysql_bridges[cfg.id]


# ---------------------------------------------------------------------------
# Inlined helpers (from server.py, avoids mcp dependency)
# ---------------------------------------------------------------------------

def _validate_yoast(focus_keyphrase: str, seo_title: str, meta_description: str) -> str | None:
    """Validate Yoast fields. Returns error string or None.
    Keyphrase check uses word overlap (not exact substring) to avoid
    rejecting titles like 'Medical Detox for Alcohol' when keyphrase is
    'medical detox alcohol'.
    """
    errors = []
    if not focus_keyphrase:
        errors.append("Focus keyphrase is required.")
    if not seo_title:
        errors.append("SEO title is required.")
    elif len(seo_title) < YOAST_TITLE_MIN or len(seo_title) > 70:
        # Allow up to 70 chars (Google shows ~60, Yoast green at 60)
        print(f"  WARNING: SEO title is {len(seo_title)} chars (ideal: {YOAST_TITLE_MIN}-{YOAST_TITLE_MAX})")
    if not meta_description:
        errors.append("Meta description is required.")
    elif len(meta_description) < YOAST_DESC_MIN or len(meta_description) > 170:
        # Allow up to 170 chars (Google shows ~160, Yoast green at 156)
        print(f"  WARNING: Meta description is {len(meta_description)} chars (ideal: {YOAST_DESC_MIN}-{YOAST_DESC_MAX})")
    # Keyphrase checks: warn but don't block publishing
    if focus_keyphrase and seo_title:
        kw_words = set(focus_keyphrase.lower().split())
        title_words = set(seo_title.lower().split())
        if len(kw_words & title_words) < len(kw_words) * 0.5:
            print(f"  WARNING: SEO title has low keyphrase overlap ({kw_words & title_words})")
    if focus_keyphrase and meta_description:
        kw_words = set(focus_keyphrase.lower().split())
        desc_words = set(meta_description.lower().split())
        if len(kw_words & desc_words) < len(kw_words) * 0.5:
            print(f"  WARNING: Meta description has low keyphrase overlap ({kw_words & desc_words})")
    return "; ".join(errors) if errors else None


def _count_words_html(html: str) -> int:
    text = re.sub(r"<[^>]+>", " ", html)
    return len(text.split())


def _db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _save_post(wp_post_id, title, slug, scheduled_time, site_id,
               post_type="new", author_wp_id=0):
    conn = _db()
    conn.execute(
        """INSERT INTO published_posts
           (wp_post_id, title, slug, scheduled_time, created_at, site_id, post_type, author_wp_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (wp_post_id, title, slug, scheduled_time, datetime.now().isoformat(),
         site_id, post_type, author_wp_id),
    )
    conn.commit()
    conn.close()


def _get_scheduled_times(site_id: str) -> set:
    conn = _db()
    rows = conn.execute(
        "SELECT scheduled_time FROM published_posts WHERE site_id=?", (site_id,)
    ).fetchall()
    conn.close()
    return {r["scheduled_time"] for r in rows}


def _extract_and_save_links(site_id, wp_post_id, content_html, pillar_pages):
    pattern = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    links = pattern.findall(content_html)
    if not links:
        return
    conn = _db()
    now = datetime.now().isoformat()
    conn.execute("DELETE FROM post_internal_links WHERE site_id=? AND wp_post_id=?", (site_id, wp_post_id))
    for href, anchor_raw in links:
        anchor = re.sub(r"<[^>]+>", "", anchor_raw).strip()
        is_pillar = 1 if any(pp in href for pp in pillar_pages) else 0
        conn.execute(
            """INSERT INTO post_internal_links
               (site_id, wp_post_id, target_url, anchor_text, is_pillar, created_at)
               VALUES (?,?,?,?,?,?)""",
            (site_id, wp_post_id, href, anchor, is_pillar, now),
        )
    conn.commit()
    conn.close()


def _log_activity(site_id, action, details=""):
    conn = _db()
    conn.execute(
        "INSERT INTO agent_activity_log (site_id, action, details, created_at) VALUES (?,?,?,?)",
        (site_id or "", action, details, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def _wp_upload_featured_image(session, wp_site_url, search_query):
    unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY", "")
    if not unsplash_key:
        return None
    try:
        resp = requests.get("https://api.unsplash.com/search/photos", params={
            "query": search_query, "per_page": 5, "content_filter": "high",
        }, headers={"Authorization": f"Client-ID {unsplash_key}"})
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        photo = results[0]
        img_url = photo["urls"]["regular"]
        alt_text = photo.get("alt_description", search_query)
        photographer = photo.get("user", {}).get("name", "Unsplash")
        img_resp = requests.get(img_url)
        img_resp.raise_for_status()
        filename = f"{search_query[:40].replace(' ', '-').lower()}.jpg"
        upload_resp = session.post(
            f"{wp_site_url}/wp-json/wp/v2/media/",
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Type": "image/jpeg"},
            data=img_resp.content,
        )
        upload_resp.raise_for_status()
        media_id = upload_resp.json().get("id")
        if media_id:
            session.post(f"{wp_site_url}/wp-json/wp/v2/media/{media_id}/", json={
                "alt_text": f"{alt_text} -- Photo by {photographer} on Unsplash",
            })
        return media_id
    except Exception:
        return None


def _mysql_upload_featured_image(bridge, search_query: str, author_id: int = 27):
    """Upload a featured image via MySQL bridge (filesystem + DB insert)."""
    unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY", "")
    if not unsplash_key:
        print("  WARN: No UNSPLASH_ACCESS_KEY set, skipping featured image")
        return None
    try:
        resp = requests.get("https://api.unsplash.com/search/photos", params={
            "query": search_query, "per_page": 5, "content_filter": "high",
        }, headers={"Authorization": f"Client-ID {unsplash_key}"})
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            print("  WARN: No Unsplash results for featured image query")
            return None
        photo = results[0]
        img_url = photo["urls"]["regular"]
        alt_text = photo.get("alt_description", search_query)
        photographer = photo.get("user", {}).get("name", "Unsplash")
        img_resp = requests.get(img_url)
        img_resp.raise_for_status()
        filename = f"{search_query[:40].replace(' ', '-').lower()}.jpg"
        full_alt = f"{alt_text} -- Photo by {photographer} on Unsplash"
        media_id = bridge.upload_media(
            image_bytes=img_resp.content,
            filename=filename,
            mime_type="image/jpeg",
            alt_text=full_alt,
            author_id=author_id,
        )
        if media_id:
            print(f"  Featured image uploaded via MySQL bridge: attachment ID {media_id}")
        else:
            print("  WARN: MySQL bridge media upload returned None")
        return media_id
    except Exception as e:
        print(f"  WARN: Featured image upload failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Google PAA (People Also Ask) scraper for FAQ generation
# ---------------------------------------------------------------------------

def scrape_google_paa(keyphrase: str, max_questions: int = 5) -> list[str]:
    """Scrape Google's 'People Also Ask' questions for a given keyphrase.

    Returns a list of PAA question strings. Falls back to empty list on failure.
    Used to generate FAQ sections that match real user queries — critical for
    appearing in AI Overviews and featured snippets.
    """
    if not keyphrase:
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        url = "https://www.google.com/search"
        params = {"q": keyphrase, "gl": "us", "hl": "en"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        html = resp.text

        # Extract PAA questions from various patterns Google uses
        paa_questions = []

        # Pattern 1: data-q attribute (common PAA format)
        for match in re.finditer(r'data-q="([^"]+)"', html):
            q = unescape(match.group(1)).strip()
            if q and q not in paa_questions and "?" in q:
                paa_questions.append(q)

        # Pattern 2: "People also ask" section with question spans
        for match in re.finditer(
            r'<span[^>]*>([^<]*\?)</span>', html
        ):
            q = unescape(match.group(1)).strip()
            if (q and q not in paa_questions and len(q) > 15 and len(q) < 200
                    and not q.startswith("http")):
                paa_questions.append(q)

        # Pattern 3: aria-label on expandable elements
        for match in re.finditer(r'aria-label="([^"]*\?)"', html):
            q = unescape(match.group(1)).strip()
            if q and q not in paa_questions and len(q) > 15:
                paa_questions.append(q)

        # Deduplicate and limit
        seen = set()
        unique = []
        for q in paa_questions:
            q_lower = q.lower().strip()
            if q_lower not in seen:
                seen.add(q_lower)
                unique.append(q)

        result = unique[:max_questions]
        if result:
            print(f"    [PAA] Found {len(result)} 'People Also Ask' questions for '{keyphrase}'")
        else:
            print(f"    [PAA] No PAA questions found for '{keyphrase}' — will use Claude-generated FAQs")
        return result

    except Exception as e:
        print(f"    [PAA] Google scrape failed (non-blocking): {e}")
        return []


# ---------------------------------------------------------------------------
# Blog generation
# ---------------------------------------------------------------------------

def load_blog_prompt() -> str:
    with open(BLOG_PROMPT_PATH, "r") as f:
        return f.read()


def build_site_context(cfg: SiteConfig, all_links: list[dict]) -> str:
    """Build context string about the site for Claude."""
    pillar_links = []
    for link in all_links:
        if link.get("is_pillar"):
            pillar_links.append(f"- [{link['title']}]({link['url']})")

    # Always include configured pillar pages
    existing_urls = {l.get("url", "").rstrip("/") for l in all_links}
    for path in (cfg.pillar_pages or []):
        full_url = f"{cfg.wp_site_url}{path}".rstrip("/")
        if full_url not in existing_urls:
            title = path.strip("/").replace("-", " ").title()
            pillar_links.append(f"- [{title}]({cfg.wp_site_url}{path})")

    blog_links = []
    for link in all_links:
        if not link.get("is_pillar"):
            blog_links.append(f"- [{link['title']}]({link['url']})")

    lines = [
        f"## Site Information",
        f"- Site: {cfg.name}",
        f"- Domain: {cfg.domain}",
        f"- Phone: {cfg.phone_number}",
        f"- URL: {cfg.wp_site_url}",
        "",
        "## Pillar Pages (MUST link to at least 2 of these):",
        *pillar_links,
        "",
        "## Existing Blog Posts (link to 1-3 of these if relevant):",
        *(blog_links[:15] if blog_links else ["- (No existing blog posts found)"]),
    ]

    if cfg.default_categories:
        lines.append(f"\n## Default Categories: {', '.join(cfg.default_categories)}")
    if cfg.default_tags:
        lines.append(f"## Existing Tags: {', '.join(cfg.default_tags)}")

    return "\n".join(lines)


def get_site_structure(cfg: SiteConfig) -> list[dict]:
    """Fetch pages and posts from WordPress for internal linking context."""
    pillar_page_paths = cfg.pillar_pages or []

    # Use MySQL bridge if configured
    if _use_mysql(cfg):
        bridge = _get_mysql_bridge(cfg)
        try:
            links = bridge.get_structure(limit=50)
            all_links = []
            for lnk in links:
                is_pillar = any(pp in lnk["url"] for pp in pillar_page_paths)
                all_links.append({"title": lnk["title"], "url": lnk["url"], "is_pillar": is_pillar})
            return all_links
        except Exception as e:
            print(f"  WARNING: MySQL bridge get_structure failed for {cfg.name}: {e}")
            return []

    s = wp_session_for_site(cfg)
    all_links = []

    try:
        # Fetch pages
        pages = _wp_get_paginated(s, f"{cfg.wp_site_url}/wp-json/wp/v2/pages")
        for p in pages:
            title = _strip_html(p.get("title", {}).get("rendered", ""))
            url = p.get("link", "")
            is_pillar = any(pp in url for pp in pillar_page_paths)
            all_links.append({"title": title, "url": url, "is_pillar": is_pillar})

        # Fetch posts
        posts = _wp_get_paginated(s, f"{cfg.wp_site_url}/wp-json/wp/v2/posts")
        for p in posts:
            title = _strip_html(p.get("title", {}).get("rendered", ""))
            url = p.get("link", "")
            all_links.append({"title": title, "url": url, "is_pillar": False})
    except Exception as e:
        print(f"  WARNING: Could not fetch site structure for {cfg.name}: {e}")

    return all_links


def generate_blog_post(question: str, topic: str, keywords: str,
                       context: str, cfg: SiteConfig, all_links: list[dict]) -> dict | None:
    """Use Claude to generate a full blog post with all required fields."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system_prompt = load_blog_prompt()
    site_context = build_site_context(cfg, all_links)

    # Scrape Google PAA for real user questions to use in FAQ section
    # This is critical for AI traffic — FAQs based on actual PAA questions
    # get picked up by AI Overviews and featured snippets
    primary_keyword = keywords.split(",")[0].strip() if keywords else question[:60]
    paa_questions = scrape_google_paa(primary_keyword)

    paa_section = ""
    if paa_questions:
        paa_section = f"""

## People Also Ask (from Google — USE THESE as FAQ questions)
The following questions come from Google's "People Also Ask" section for this topic.
You MUST use ALL of these as FAQ items in your FAQ section. Answer each one in 2-4 sentences.
You may add 1-2 more questions to reach 4-6 total.

{chr(10).join(f'- {q}' for q in paa_questions)}"""
    else:
        paa_section = """

## FAQ Instructions
No PAA questions were found for this topic. Generate 4-6 FAQ questions that someone searching
this keyword would naturally ask. Make them conversational and keyword-rich."""

    user_message = f"""## Client Question
{question}

## Topic
{topic}

## Keywords
{keywords}

## Context from the Call
{context}

## Available Internal Links
{site_context}
{paa_section}

Write the blog post now. Return ONLY the JSON object with all required fields including faq_schema and article_schema."""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        print(f"  ERROR calling Claude API: {e}")
        return None

    text = response.content[0].text.strip()

    # Parse JSON response
    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        # First try strict parsing
        try:
            post = json.loads(text)
        except json.JSONDecodeError:
            # Allow control characters (common in HTML content from Claude)
            post = json.loads(text, strict=False)
    except json.JSONDecodeError as e:
        # Last resort: try to extract JSON from the response
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                post = json.loads(json_match.group(), strict=False)
            except json.JSONDecodeError as e2:
                print(f"  ERROR parsing Claude JSON: {e2}")
                print(f"  First 300 chars: {text[:300]}")
                print(f"  Last 200 chars: {text[-200:]}")
                print(f"  Length: {len(text)}, stop_reason: {response.stop_reason}")
                return None
        else:
            print(f"  ERROR parsing Claude JSON: {e}")
            print(f"  First 300 chars: {text[:300]}")
            print(f"  Length: {len(text)}, stop_reason: {response.stop_reason}")
            return None

    # Validate required keys
    required = [
        "title", "slug", "content_html", "categories", "tags",
        "focus_keyphrase", "seo_title", "meta_description", "excerpt",
        "featured_image_query",
    ]
    missing = [k for k in required if k not in post]
    if missing:
        print(f"  ERROR: Missing keys in blog response: {missing}")
        return None

    # Ensure faq_schema exists (generate from content if Claude didn't return it)
    if "faq_schema" not in post or not post["faq_schema"]:
        print("    WARN: faq_schema missing from Claude response — extracting from content")
        post["faq_schema"] = _extract_faq_from_html(post["content_html"])

    # Ensure article_schema exists
    if "article_schema" not in post or not post["article_schema"]:
        post["article_schema"] = {
            "headline": post["title"],
            "description": post.get("meta_description", post.get("excerpt", "")),
            "keywords": post.get("focus_keyphrase", ""),
        }

    return post


# ---------------------------------------------------------------------------
# Duplicate checking (mirrors server.py:check_duplicate_topics)
# ---------------------------------------------------------------------------

def check_duplicate_topics(cfg: SiteConfig, proposed_topic: str) -> bool:
    """Check if a very similar post already exists on WordPress.

    Returns True if a near-exact duplicate exists (skip this question).
    Returns False if no close match found (safe to create a new post).

    NOTE: We NEVER update existing posts. We only skip questions that are
    already covered by an existing post with a very similar title.
    """
    # Use MySQL bridge if configured
    if _use_mysql(cfg):
        bridge = _get_mysql_bridge(cfg)
        try:
            result = bridge.check_duplicate(proposed_topic)
            if result["is_duplicate"]:
                print(f"    SKIP duplicate: '{result['match_title']}' (MySQL bridge)")
            return result["is_duplicate"]
        except Exception as e:
            print(f"  WARNING: MySQL duplicate check failed: {e}")
            return False

    s = wp_session_for_site(cfg)
    base = f"{cfg.wp_site_url}/wp-json/wp/v2"
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00")

    try:
        search_resp = s.get(f"{base}/posts", params={
            "search": proposed_topic,
            "after": one_year_ago,
            "per_page": 5,
            "status": "publish,future",
            "orderby": "relevance",
        })
        search_resp.raise_for_status()
        results = search_resp.json()
    except Exception as e:
        print(f"  WARNING: Duplicate check failed: {e}")
        return False

    # Only consider it a duplicate if the title shares 60%+ of significant words
    topic_words = {w.lower() for w in proposed_topic.split() if len(w) >= 4}
    if not topic_words:
        return False

    for post in results:
        title = _strip_html(post.get("title", {}).get("rendered", ""))
        title_words = {w.lower() for w in title.split() if len(w) >= 4}
        if not title_words:
            continue
        overlap = len(topic_words & title_words)
        ratio = overlap / len(topic_words)
        if ratio >= 0.6:
            print(f"    SKIP duplicate: '{title}' ({ratio:.0%} overlap)")
            return True

    return False


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

def get_next_publish_slot(cfg: SiteConfig) -> str:
    """Find the next available publish slot for a site."""
    tz = ZoneInfo(cfg.timezone)
    now = datetime.now(tz)
    scheduled = _get_scheduled_times(cfg.id)
    publish_times = cfg.publish_times or ["09:00", "14:00"]

    for day_offset in range(30):
        day = now.date() + timedelta(days=day_offset)
        for time_str in publish_times:
            hour, minute = map(int, time_str.split(":"))
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            if slot <= now:
                continue
            slot_iso = slot.isoformat()
            if slot_iso not in scheduled:
                return slot_iso

    raise RuntimeError(f"No available publish slots for {cfg.name} in the next 30 days.")


def publish_blog_post(cfg: SiteConfig, post_data: dict, question_id: int,
                      dry_run: bool = False) -> dict | None:
    """Validate, enrich, and publish a blog post to WordPress."""
    site_id = cfg.id

    # -- Validate Yoast
    yoast_err = _validate_yoast(
        post_data.get("focus_keyphrase", ""),
        post_data.get("seo_title", ""),
        post_data.get("meta_description", ""),
    )
    if yoast_err:
        print(f"  YOAST FAIL: {yoast_err}")
        return None

    # -- Validate excerpt
    excerpt = post_data.get("excerpt", "").strip()
    if not excerpt or len(excerpt) < 10:
        print(f"  FAIL: Excerpt missing or too short.")
        return None

    # -- Validate word count
    content_html = post_data["content_html"]
    word_count = _count_words_html(content_html)
    min_words = cfg.min_word_count or 1000
    if word_count < min_words:
        print(f"  FAIL: Only {word_count} words (minimum {min_words}).")
        return None

    # -- Get publish slot
    try:
        scheduled_time = get_next_publish_slot(cfg)
    except RuntimeError as e:
        print(f"  FAIL: {e}")
        return None

    if dry_run:
        print(f"  DRY RUN: Would publish '{post_data['title']}' at {scheduled_time}")
        print(f"    Words: {word_count}, Keyphrase: {post_data.get('focus_keyphrase')}")
        return {"dry_run": True, "title": post_data["title"], "scheduled_time": scheduled_time}

    author_id = cfg.default_author_id
    pillar_pages = cfg.pillar_pages or []

    # -- CTA injection
    cta_html = cfg.cta_html or ""
    if cta_html:
        combined_text = (post_data["title"] + " " + post_data["slug"]).lower()
        is_cta_relevant = False
        for pp in pillar_pages:
            pp_words = pp.strip("/").replace("-", " ").lower()
            if any(w in combined_text for w in pp_words.split() if len(w) >= 4):
                is_cta_relevant = True
                break
        if is_cta_relevant:
            paragraphs = content_html.split("</p>")
            if len(paragraphs) > 3:
                insert_point = len(paragraphs) // 3
                paragraphs.insert(insert_point, f"</p>{cta_html}")
            content_html = "</p>".join(paragraphs) + cta_html

    # -- Inject structured data (Article + FAQ schema) into content
    schema_html = _build_schema_json_ld(
        post_data, cfg, scheduled_time,
        post_url=f"https://{cfg.domain}/blog/{post_data['slug']}/",
    )
    if schema_html:
        content_html += "\n" + schema_html
        faq_count = len(post_data.get("faq_schema", []))
        print(f"    Schema injected: Article + FAQ ({faq_count} Q&As)")

    # -- Convert scheduled_time to MySQL format (YYYY-MM-DD HH:MM:SS)
    try:
        sched_dt = datetime.fromisoformat(scheduled_time)
        sched_mysql = sched_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        sched_mysql = scheduled_time

    # =========================================================================
    # MySQL bridge path (for sites where HTTP to own domain hangs)
    # =========================================================================
    if _use_mysql(cfg):
        bridge = _get_mysql_bridge(cfg)

        # -- Featured image from Unsplash (MySQL bridge path)
        featured_media_id = None
        img_query = post_data.get("featured_image_query", "")
        if img_query:
            featured_media_id = _mysql_upload_featured_image(bridge, img_query, author_id or 27)

        try:
            create_data = {
                "title": post_data["title"],
                "content_html": content_html,
                "slug": post_data["slug"],
                "excerpt": excerpt,
                "status": "future",
                "date": sched_mysql,
                "author_id": author_id or 27,
                "categories": post_data.get("categories", cfg.default_categories or []),
                "tags": post_data.get("tags", cfg.default_tags or []),
                "focus_keyphrase": post_data.get("focus_keyphrase", ""),
                "seo_title": post_data.get("seo_title", ""),
                "meta_description": post_data.get("meta_description", ""),
            }
            if featured_media_id:
                create_data["featured_media"] = featured_media_id

            result = bridge.create_post(create_data)
            wp_id = result["id"]
            wp_post = result
        except Exception as e:
            print(f"  ERROR publishing via MySQL bridge: {e}")
            return None
    else:
        # =====================================================================
        # Standard WP REST API path
        # =====================================================================
        s = wp_session_for_site(cfg)
        base = f"{cfg.wp_site_url}/wp-json/wp/v2"
        # NOTE: Some hosts (e.g. Nova behind LiteSpeed) redirect non-trailing-slash
        # URLs with a 301, which converts POST→GET and silently breaks writes.
        # Always use trailing slashes on POST endpoints.

        # -- Resolve category IDs
        cat_ids = []
        for name in post_data.get("categories", cfg.default_categories or []):
            try:
                existing = s.get(f"{base}/categories", params={"search": name}).json()
                if existing:
                    cat_ids.append(existing[0]["id"])
                else:
                    resp = s.post(f"{base}/categories/", json={"name": name})
                    if resp.ok:
                        cat_ids.append(resp.json()["id"])
            except Exception:
                pass
            time.sleep(0.3)

        # -- Resolve tag IDs
        tag_ids = []
        for name in post_data.get("tags", cfg.default_tags or []):
            try:
                existing = s.get(f"{base}/tags", params={"search": name}).json()
                if existing:
                    tag_ids.append(existing[0]["id"])
                else:
                    resp = s.post(f"{base}/tags/", json={"name": name})
                    if resp.ok:
                        tag_ids.append(resp.json()["id"])
            except Exception:
                pass
            time.sleep(0.3)

        # -- Featured image from Unsplash
        featured_media_id = None
        img_query = post_data.get("featured_image_query", "")
        if img_query:
            featured_media_id = _wp_upload_featured_image(s, cfg.wp_site_url, img_query)

        # -- Create scheduled post
        payload = {
            "title": post_data["title"],
            "content": content_html,
            "slug": post_data["slug"],
            "categories": cat_ids,
            "tags": tag_ids,
            "status": "future",
            "date": scheduled_time,
            "author": author_id,
            "excerpt": excerpt,
            "meta": {
                "_yoast_wpseo_focuskw": post_data.get("focus_keyphrase", ""),
                "_yoast_wpseo_title": post_data.get("seo_title", ""),
                "_yoast_wpseo_metadesc": post_data.get("meta_description", ""),
            },
        }
        if featured_media_id:
            payload["featured_media"] = featured_media_id

        try:
            resp = s.post(f"{base}/posts/", json=payload)
            resp.raise_for_status()
            wp_post = resp.json()
        except Exception as e:
            print(f"  ERROR publishing to WordPress: {e}")
            return None

        wp_id = wp_post.get("id", 0)

    # -- Save to DB
    _save_post(wp_id, post_data["title"], post_data["slug"],
               scheduled_time, site_id, post_type="new", author_wp_id=author_id)

    _extract_and_save_links(site_id, wp_id, content_html, pillar_pages)

    db.mark_question_used(question_id)

    _log_activity(site_id, "pipeline_publish",
                  f"wp_post_id={wp_id} title='{post_data['title']}' scheduled={scheduled_time}")

    # Fire-and-forget webhook to CMO dashboard
    _notify_cmo_dashboard(
        brand=site_id,
        title=post_data["title"],
        url=wp_post.get("link", ""),
        keyword=post_data.get("focus_keyphrase", ""),
        published_at=scheduled_time,
        excerpt=post_data.get("excerpt", ""),
        word_count=word_count,
    )

    return {
        "wp_post_id": wp_id,
        "title": post_data["title"],
        "url": wp_post.get("link", ""),
        "scheduled_for": scheduled_time,
        "word_count": word_count,
        "featured_image": "set" if featured_media_id else "none",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_faq_from_html(content_html: str) -> list[dict]:
    """Extract FAQ Q&A pairs from <details>/<summary> HTML if Claude didn't return faq_schema."""
    faqs = []
    for match in re.finditer(
        r'<summary>(.*?)</summary>\s*<p>(.*?)</p>', content_html, re.DOTALL
    ):
        q = _strip_html(match.group(1)).strip()
        a = _strip_html(match.group(2)).strip()
        if q and a:
            faqs.append({"question": q, "answer": a})
    return faqs


def _build_schema_json_ld(post_data: dict, cfg, scheduled_time: str, post_url: str) -> str:
    """Build JSON-LD structured data for Article + FAQ schema.

    Returns a <script type="application/ld+json"> block to append to content_html.
    This is critical for AI Overview visibility and rich search results.
    """
    schemas = []

    # --- Article Schema ---
    article_info = post_data.get("article_schema", {})
    article_schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article_info.get("headline", post_data["title"]),
        "description": article_info.get("description", post_data.get("meta_description", "")),
        "keywords": article_info.get("keywords", post_data.get("focus_keyphrase", "")),
        "datePublished": scheduled_time,
        "dateModified": scheduled_time,
        "author": {
            "@type": "Organization",
            "name": cfg.name,
            "url": f"https://{cfg.domain}",
        },
        "publisher": {
            "@type": "Organization",
            "name": cfg.name,
            "url": f"https://{cfg.domain}",
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": post_url or f"https://{cfg.domain}/blog/{post_data['slug']}/",
        },
    }
    schemas.append(article_schema)

    # --- FAQ Schema ---
    faq_items = post_data.get("faq_schema", [])
    if faq_items:
        faq_schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": item["question"],
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": item["answer"],
                    },
                }
                for item in faq_items
                if item.get("question") and item.get("answer")
            ],
        }
        schemas.append(faq_schema)

    # Build the script tags
    script_blocks = []
    for schema in schemas:
        script_blocks.append(
            f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>'
        )

    return "\n".join(script_blocks)


def _notify_cmo_dashboard(brand: str, title: str, url: str, keyword: str,
                          published_at: str = "", excerpt: str = "",
                          word_count: int = 0):
    """Fire-and-forget webhook to CMO dashboard after each publish.
    Never raises — logs errors and returns silently."""
    cmo_key = os.environ.get("CMO_WEBHOOK_KEY", "")
    if not cmo_key:
        return  # Not configured — skip silently

    payload = {
        "brand": brand,
        "title": title,
        "url": url,
        "keyword": keyword,
        "published_at": published_at or datetime.now().isoformat(),
    }
    if excerpt:
        payload["excerpt"] = excerpt
    if word_count:
        payload["word_count"] = word_count

    try:
        resp = requests.post(
            "http://32.193.178.164/api/cmo/blog-published",
            json=payload,
            headers={"X-CMO-Key": cmo_key, "Content-Type": "application/json"},
            timeout=5,
        )
        if resp.status_code == 201:
            print(f"    [CMO webhook] Notified: {brand} — '{title}'")
        else:
            print(f"    [CMO webhook] Non-201 response: {resp.status_code}")
    except Exception as e:
        print(f"    [CMO webhook] Failed (non-blocking): {e}")


def _strip_html(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", html or "")).strip()


def _wp_get_paginated(session, url: str, params: dict | None = None,
                      max_items: int = 50) -> list[dict]:
    """Fetch items from a paginated WP REST API endpoint.

    Caps at max_items to avoid fetching thousands of pages for large sites
    (e.g. Briarwood with 1000+ pages) which would overwhelm Claude's context.
    """
    params = params or {}
    params.setdefault("per_page", 50)
    all_items, page = [], 1
    while len(all_items) < max_items:
        params["page"] = page
        resp = session.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        all_items.extend(data)
        total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
        if page >= total_pages:
            break
        page += 1
    return all_items[:max_items]


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(site_filter: str | None = None, max_posts: int | None = None,
                 dry_run: bool = False, skip_calls: bool = False):
    """Run the full automated pipeline."""
    max_posts = max_posts or MAX_POSTS_PER_SITE
    start_time = datetime.now()

    print("=" * 70)
    print(f"Eudaimonia Blog Agent — Pipeline Runner")
    print(f"Started: {start_time.isoformat()}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print("=" * 70)

    # 1. Init DB
    db.init_db()

    # 2. Load sites
    all_sites = get_all_sites()
    if site_filter:
        all_sites = [s for s in all_sites if s.id == site_filter]
        if not all_sites:
            print(f"ERROR: Site '{site_filter}' not found.")
            sys.exit(1)

    active_sites = [s for s in all_sites if s.active and s.wp_username]
    print(f"\nActive sites with WP creds: {[s.id for s in active_sites]}")

    if not active_sites:
        print("No active sites with WordPress credentials configured. Exiting.")
        return

    # 2b. Auto-generate questions for sites with no pending content
    print(f"\n--- Phase 0: Auto-Seeding Blog Topics ---")
    for cfg in active_sites:
        pending = db.get_pending_questions(limit=10, site_id=cfg.id)
        if len(pending) >= max_posts:
            print(f"  {cfg.id}: {len(pending)} pending questions — enough for this run")
            continue

        needed = max_posts - len(pending)
        print(f"  {cfg.id}: {len(pending)} pending, generating {needed} new topic(s)...")

        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            # Get existing published titles to avoid duplicates
            existing_titles = db.get_recent_published_titles(site_id=cfg.id, limit=50)
            existing_list = "\n".join(f"- {t}" for t in existing_titles) if existing_titles else "None yet"

            resp = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": f"""Generate {needed} unique blog topic questions for {cfg.name} ({cfg.domain}).

Site niche: {', '.join(json.loads(cfg.default_categories) if isinstance(cfg.default_categories, str) else (cfg.default_categories or ['recovery']))}
Location: Austin, Texas
Target audience: People searching for help with addiction, detox, rehab, or sober living

Already published (DO NOT repeat these topics):
{existing_list}

For each topic, provide a JSON array of objects with:
- "question": A specific question someone would search on Google (People Also Ask style)
- "topic": Short topic label (2-4 words)
- "keywords": Comma-separated SEO keywords (3-5 keywords)

Return ONLY the JSON array, no other text. Make the questions specific, actionable, and different from what's already published."""}],
            )

            topics_text = resp.content[0].text.strip()
            if topics_text.startswith("```"):
                topics_text = topics_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            topics = json.loads(topics_text, strict=False)

            for t in topics[:needed]:
                db.save_question(
                    call_id=f"auto_{cfg.id}_{int(time.time())}_{topics.index(t)}",
                    question=t["question"],
                    topic=t.get("topic", ""),
                    keywords=t.get("keywords", ""),
                    context="Auto-generated daily topic",
                    site_id=cfg.id,
                )
                print(f"    Seeded: {t['question'][:80]}")

        except Exception as e:
            print(f"  ERROR auto-generating topics for {cfg.id}: {e}")

    # 3. Fetch and process RingCentral calls
    if not skip_calls:
        print(f"\n--- Phase 1: Fetching RingCentral Calls (past {RC_CALL_LOG_DAYS} days) ---")
        try:
            rc = RingCentralClient()
            rc.login()

            # Pre-filter: get already-processed call IDs so we skip them
            # BEFORE downloading recordings (avoids RC rate limits)
            processed_ids = db.get_all_processed_call_ids()
            print(f"  Already processed: {len(processed_ids)} calls in DB")

            calls = rc.get_calls_with_transcripts(
                days=RC_CALL_LOG_DAYS,
                skip_call_ids=processed_ids,
            )
            print(f"  Got {len(calls)} NEW calls with transcripts.")

            for call in calls:
                call_id = call["call_id"]

                # Route to site by destination phone number (which site was called)
                to_number = call.get("to_number", "")
                caller = call.get("caller_info", "")
                site_id = get_site_by_phone(to_number) or get_site_by_phone(caller) or DEFAULT_SITE
                print(f"\n  Processing call {call_id} (to: {to_number}, caller: {caller}) → site: {site_id}")

                questions = analyze_transcript(call_id, call["transcript"], site_id=site_id)
                print(f"    Extracted {len(questions)} questions.")

        except Exception as e:
            print(f"  ERROR fetching RingCentral calls: {e}")
            print("  Continuing with pending questions from previous runs...")
    else:
        print("\n--- Phase 1: SKIPPED (--skip-calls) ---")

    # 4. Generate and publish blog posts per site
    print(f"\n--- Phase 2: Generating & Publishing Blog Posts ---")

    summary = {"total_published": 0, "total_skipped": 0, "total_failed": 0, "sites": {}}

    for cfg in active_sites:
        print(f"\n  === Site: {cfg.name} ({cfg.id}) ===")

        # Get pending questions for this site
        pending = db.get_pending_questions(limit=max_posts, site_id=cfg.id)
        print(f"  Pending questions: {len(pending)}")

        if not pending:
            summary["sites"][cfg.id] = {"published": 0, "skipped": 0, "failed": 0}
            continue

        # Fetch site structure for internal linking
        print(f"  Fetching site structure for internal links...")
        all_links = get_site_structure(cfg)
        print(f"  Found {len(all_links)} pages/posts for internal linking.")

        site_published = 0
        site_skipped = 0
        site_failed = 0

        for q in pending:
            if site_published >= max_posts:
                break

            question_text = q["question"]
            print(f"\n  Processing: {question_text[:60]}...")

            # Check for duplicate topics — skip if already covered
            is_duplicate = check_duplicate_topics(cfg, question_text)
            if is_duplicate:
                db.mark_question_used(q["id"])
                site_skipped += 1
                continue

            # Generate blog post via Claude
            post_data = generate_blog_post(
                question=question_text,
                topic=q.get("topic", ""),
                keywords=q.get("keywords", ""),
                context=q.get("context", ""),
                cfg=cfg,
                all_links=all_links,
            )

            if not post_data:
                print(f"    SKIP: Failed to generate blog post.")
                site_failed += 1
                continue

            word_count = _count_words_html(post_data["content_html"])
            print(f"    Generated: '{post_data['title']}' ({word_count} words)")

            # Always create a NEW post — never update existing ones
            result = publish_blog_post(cfg, post_data, q["id"], dry_run=dry_run)
            if result:
                site_published += 1
                if not dry_run:
                    print(f"    OK: Published WP#{result['wp_post_id']} → {result['scheduled_for']}")
            else:
                site_failed += 1

        summary["sites"][cfg.id] = {
            "published": site_published,
            "skipped": site_skipped,
            "failed": site_failed,
        }
        summary["total_published"] += site_published
        summary["total_skipped"] += site_skipped
        summary["total_failed"] += site_failed

    # 5. Print summary
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n{'=' * 70}")
    print(f"Pipeline Complete — {elapsed:.0f}s elapsed")
    print(f"{'=' * 70}")
    print(f"  New posts:     {summary['total_published']}")
    print(f"  Skipped dupes: {summary['total_skipped']}")
    print(f"  Failed:        {summary['total_failed']}")
    for site_id, stats in summary["sites"].items():
        print(f"  [{site_id}] published={stats['published']} skipped={stats['skipped']} failed={stats['failed']}")
    print(f"{'=' * 70}\n")

    _log_activity(None, "pipeline_run_complete", json.dumps(summary))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eudaimonia Blog Agent — Automated Pipeline Runner")
    parser.add_argument("--site", type=str, default=None,
                        help="Run for a specific site only (e.g., eudaimonia, nova, briarwood)")
    parser.add_argument("--max-posts", type=int, default=None,
                        help=f"Max posts per site per run (default: {MAX_POSTS_PER_SITE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without actually publishing")
    parser.add_argument("--skip-calls", action="store_true",
                        help="Skip RingCentral call fetching; only process pending questions")
    args = parser.parse_args()

    run_pipeline(
        site_filter=args.site,
        max_posts=args.max_posts,
        dry_run=args.dry_run,
        skip_calls=args.skip_calls,
    )
