"""
Eudaimonia Blog Agent -- MCP Server (multi-site)
Tools for pulling RingCentral call recordings and publishing blogs to WordPress.
Claude Code handles the AI analysis and writing.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from ringcentral import SDK

from modules.site_config import (
    SiteConfig,
    get_all_sites,
    get_site,
    get_site_authors as _get_site_authors_db,
    wp_session_for_site,
)

# Load env
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

RC_CLIENT_ID = os.getenv("RC_CLIENT_ID")
RC_CLIENT_SECRET = os.getenv("RC_CLIENT_SECRET")
RC_JWT_TOKEN = os.getenv("RC_JWT_TOKEN")
RC_SERVER = os.getenv("RC_SERVER", "https://platform.ringcentral.com")

DB_PATH = Path(__file__).resolve().parent / "db" / "tracker.db"

MIN_WORD_COUNT = 1000

# Yoast SEO green-light ranges
YOAST_TITLE_MIN = 30
YOAST_TITLE_MAX = 60
YOAST_DESC_MIN = 120
YOAST_DESC_MAX = 156


# ---------------------------------------------------------------------------
# Validation helpers (unchanged)
# ---------------------------------------------------------------------------

def _validate_yoast(focus_keyphrase: str, seo_title: str, meta_description: str) -> str | None:
    """Validate Yoast fields meet green-light requirements. Returns error string or None."""
    errors = []
    if not focus_keyphrase:
        errors.append("Focus keyphrase is required.")
    if not seo_title:
        errors.append("SEO title is required.")
    elif len(seo_title) < YOAST_TITLE_MIN or len(seo_title) > YOAST_TITLE_MAX:
        errors.append(
            f"SEO title is {len(seo_title)} chars. "
            f"For Yoast green, keep it between {YOAST_TITLE_MIN}-{YOAST_TITLE_MAX} chars."
        )
    if focus_keyphrase and seo_title and focus_keyphrase.lower() not in seo_title.lower():
        errors.append("SEO title must contain the focus keyphrase.")
    if not meta_description:
        errors.append("Meta description is required.")
    elif len(meta_description) < YOAST_DESC_MIN or len(meta_description) > YOAST_DESC_MAX:
        errors.append(
            f"Meta description is {len(meta_description)} chars. "
            f"For Yoast green, keep it between {YOAST_DESC_MIN}-{YOAST_DESC_MAX} chars."
        )
    if focus_keyphrase and meta_description and focus_keyphrase.lower() not in meta_description.lower():
        errors.append("Meta description must contain the focus keyphrase.")
    return "; ".join(errors) if errors else None


def _count_words_html(html: str) -> int:
    """Count words in HTML content, stripping all tags."""
    text = re.sub(r"<[^>]+>", " ", html)
    return len(text.split())


# ---------------------------------------------------------------------------
# Site config helper
# ---------------------------------------------------------------------------

def _require_site(site_id: str) -> SiteConfig:
    """Return SiteConfig or raise ValueError."""
    cfg = get_site(site_id)
    if not cfg:
        raise ValueError(f"Unknown site_id '{site_id}'. Use list_sites() to see valid IDs.")
    return cfg


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS processed_calls (
        call_id TEXT PRIMARY KEY, processed_at TEXT, site_id TEXT DEFAULT '')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS published_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wp_post_id INTEGER, title TEXT, slug TEXT,
        scheduled_time TEXT, created_at TEXT,
        site_id TEXT DEFAULT '', post_type TEXT DEFAULT 'new',
        source_transcript TEXT DEFAULT '', author_wp_id INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS post_internal_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT NOT NULL, wp_post_id INTEGER NOT NULL,
        target_url TEXT NOT NULL, anchor_text TEXT DEFAULT '',
        is_pillar INTEGER DEFAULT 0, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT, action TEXT NOT NULL,
        details TEXT DEFAULT '', created_at TEXT NOT NULL)""")
    conn.commit()
    return conn


def _is_call_processed(call_id: str) -> bool:
    conn = _db()
    row = conn.execute("SELECT 1 FROM processed_calls WHERE call_id=?", (call_id,)).fetchone()
    conn.close()
    return row is not None


def _mark_call_processed(call_id: str, site_id: str = ""):
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO processed_calls (call_id, processed_at, site_id) VALUES (?,?,?)",
        (call_id, datetime.now().isoformat(), site_id),
    )
    conn.commit()
    conn.close()


def _save_post(
    wp_post_id: int,
    title: str,
    slug: str,
    scheduled_time: str,
    site_id: str,
    post_type: str = "new",
    author_wp_id: int = 0,
):
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


def _get_scheduled_times(site_id: str) -> set[str]:
    conn = _db()
    rows = conn.execute(
        "SELECT scheduled_time FROM published_posts WHERE site_id=?", (site_id,)
    ).fetchall()
    conn.close()
    return {r["scheduled_time"] for r in rows}


# ---------------------------------------------------------------------------
# Internal-link extraction
# ---------------------------------------------------------------------------

def _extract_and_save_links(site_id: str, wp_post_id: int, content_html: str, pillar_pages: list[str]):
    """Parse <a href=...> from HTML and save to post_internal_links table."""
    pattern = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    links = pattern.findall(content_html)
    if not links:
        return

    conn = _db()
    now = datetime.now().isoformat()
    # Remove old links for this post so we get a fresh snapshot
    conn.execute(
        "DELETE FROM post_internal_links WHERE site_id=? AND wp_post_id=?",
        (site_id, wp_post_id),
    )
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


# ---------------------------------------------------------------------------
# Activity logging
# ---------------------------------------------------------------------------

def _log_activity(site_id: str | None, action: str, details: str = ""):
    conn = _db()
    conn.execute(
        "INSERT INTO agent_activity_log (site_id, action, details, created_at) VALUES (?,?,?,?)",
        (site_id or "", action, details, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# WordPress helpers
# ---------------------------------------------------------------------------

def _wp_upload_featured_image(session, wp_site_url: str, search_query: str) -> int | None:
    """Search Unsplash for a realistic photo and upload to WP media library."""
    unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY", "")
    if not unsplash_key:
        return None

    try:
        resp = requests.get("https://api.unsplash.com/search/photos", params={
            "query": search_query,
            "per_page": 5,
            "content_filter": "high",
            "order_by": "relevant",
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
        img_bytes = img_resp.content

        filename = f"{search_query[:40].replace(' ', '-').lower()}.jpg"
        upload_resp = session.post(
            f"{wp_site_url}/wp-json/wp/v2/media",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "image/jpeg",
            },
            data=img_bytes,
        )
        upload_resp.raise_for_status()
        media_id = upload_resp.json().get("id")

        if media_id:
            session.post(f"{wp_site_url}/wp-json/wp/v2/media/{media_id}", json={
                "alt_text": f"{alt_text} -- Photo by {photographer} on Unsplash",
            })
        return media_id
    except Exception:
        return None


def _wp_get_all(session, wp_site_url: str, endpoint: str, params: dict | None = None) -> list[dict]:
    url = f"{wp_site_url}/wp-json/wp/v2/{endpoint}"
    params = params or {}
    params.setdefault("per_page", 20)
    all_items, page = [], 1
    while True:
        params["page"] = page
        resp = session.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        all_items.extend(data)
        total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
        if page >= total_pages:
            break
        page += 1
    return all_items


# ---------------------------------------------------------------------------
# RingCentral helpers
# ---------------------------------------------------------------------------

def _rc_platform():
    sdk = SDK(RC_CLIENT_ID, RC_CLIENT_SECRET, RC_SERVER)
    platform = sdk.platform()
    platform.login(jwt=RC_JWT_TOKEN)
    return platform


# ==========================================================================
# MCP Server
# ==========================================================================

mcp = FastMCP("eudaimonia-blog-agent")


# ---------------------------------------------------------------------------
# New multi-site tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_sites() -> str:
    """List all active sites managed by this agent.
    Returns site_id, name, domain, and key config for each site.
    """
    sites = get_all_sites()
    result = []
    for s in sites:
        result.append({
            "site_id": s.id,
            "name": s.name,
            "domain": s.domain,
            "wp_site_url": s.wp_site_url,
            "default_author_id": s.default_author_id,
            "phone_number": s.phone_number,
            "timezone": s.timezone,
            "publish_times": s.publish_times,
            "min_word_count": s.min_word_count,
            "pillar_pages": s.pillar_pages,
            "active": s.active,
        })
    _log_activity(None, "list_sites", f"Returned {len(result)} sites")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_site_authors_tool(site_id: str) -> str:
    """Get the list of WordPress authors configured for a site.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
    """
    _require_site(site_id)
    authors = _get_site_authors_db(site_id)
    _log_activity(site_id, "get_site_authors", f"Returned {len(authors)} authors")
    return json.dumps(authors, indent=2)


@mcp.tool()
def assign_call_to_site(call_id: str, site_id: str) -> str:
    """Assign a RingCentral call to a specific site so blog posts are published there.

    Args:
        call_id: The RingCentral call ID.
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
    """
    cfg = _require_site(site_id)
    conn = _db()
    # Update existing processed_calls row or insert
    row = conn.execute("SELECT 1 FROM processed_calls WHERE call_id=?", (call_id,)).fetchone()
    if row:
        conn.execute("UPDATE processed_calls SET site_id=? WHERE call_id=?", (site_id, call_id))
    else:
        conn.execute(
            "INSERT INTO processed_calls (call_id, processed_at, site_id) VALUES (?,?,?)",
            (call_id, datetime.now().isoformat(), site_id),
        )
    conn.commit()
    conn.close()
    _log_activity(site_id, "assign_call_to_site", f"call_id={call_id}")
    return json.dumps({
        "success": True,
        "call_id": call_id,
        "site_id": site_id,
        "site_name": cfg.name,
    }, indent=2)


# ---------------------------------------------------------------------------
# RingCentral tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_recent_calls(site_id: str, days: int = 7) -> str:
    """Fetch recent RingCentral calls that have recordings.
    Downloads audio files to the recordings/ folder.
    Returns call metadata: caller, time, duration, and local audio file path.
    Skips calls that have already been processed.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
        days: Number of days to look back (default 7).
    """
    _require_site(site_id)
    platform = _rc_platform()
    date_from = (datetime.now(tz=ZoneInfo("UTC")) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    resp = platform.get("/account/~/call-log", {
        "dateFrom": date_from, "recordingType": "All",
        "view": "Detailed", "perPage": 100,
    })
    records = resp.json_dict().get("records", [])

    recordings_dir = Path(__file__).resolve().parent / "recordings"
    recordings_dir.mkdir(exist_ok=True)

    results = []
    for call in records:
        call_id = call.get("id", "")
        if _is_call_processed(call_id):
            continue

        recording = call.get("recording")
        if not recording or not recording.get("contentUri"):
            continue

        caller = call.get("from", {})
        caller_info = caller.get("name") or caller.get("phoneNumber", "unknown")
        duration = call.get("duration", 0)

        audio_path = recordings_dir / f"{call_id}.mp3"
        if not audio_path.exists():
            try:
                audio_resp = platform.get(recording["contentUri"])
                audio_data = audio_resp.response().content
                audio_path.write_bytes(audio_data)
            except Exception as e:
                results.append({"call_id": call_id, "error": f"Download failed: {e}"})
                continue

        results.append({
            "call_id": call_id,
            "timestamp": call.get("startTime", ""),
            "caller": caller_info,
            "duration_seconds": duration,
            "audio_file": str(audio_path),
        })

    _log_activity(site_id, "get_recent_calls", f"Found {len(results)} new calls (days={days})")

    if not results:
        return "No new calls with recordings found."
    return json.dumps(results, indent=2)


@mcp.tool()
def mark_call_done(site_id: str, call_id: str) -> str:
    """Mark a call as processed so it won't appear in future get_recent_calls results.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
        call_id: The RingCentral call ID.
    """
    _require_site(site_id)
    _mark_call_processed(call_id, site_id)
    _log_activity(site_id, "mark_call_done", f"call_id={call_id}")
    return f"Call {call_id} marked as processed for site '{site_id}'."


# ---------------------------------------------------------------------------
# WordPress content tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_site_structure(site_id: str) -> str:
    """Get all pages and posts from a site for internal linking.
    Returns pillar pages, blog posts, categories, and tags.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
    """
    cfg = _require_site(site_id)
    s = wp_session_for_site(cfg)

    pages = _wp_get_all(s, cfg.wp_site_url, "pages")
    posts = _wp_get_all(s, cfg.wp_site_url, "posts")
    categories = _wp_get_all(s, cfg.wp_site_url, "categories")
    tags = _wp_get_all(s, cfg.wp_site_url, "tags")

    def strip_html(html: str) -> str:
        return unescape(re.sub(r"<[^>]+>", " ", html)).strip()

    pillar_pages_config = cfg.pillar_pages or []

    pillar_pages_list = []
    for p in pages:
        title = strip_html(p.get("title", {}).get("rendered", ""))
        url = p.get("link", "")
        is_pillar = any(pp in url for pp in pillar_pages_config)
        pillar_pages_list.append({"title": title, "url": url, "is_pillar": is_pillar})

    blog_posts = []
    for p in posts:
        title = strip_html(p.get("title", {}).get("rendered", ""))
        blog_posts.append({"title": title, "url": p.get("link", "")})

    cat_list = [{"id": c["id"], "name": c["name"]} for c in categories]
    tag_list = [{"id": t["id"], "name": t["name"], "slug": t["slug"]} for t in tags]

    pillar_urls = [f"{cfg.wp_site_url}{p}" for p in pillar_pages_config]

    _log_activity(site_id, "get_site_structure",
                  f"{len(pages)} pages, {len(posts)} posts, {len(categories)} cats, {len(tags)} tags")

    return json.dumps({
        "site_id": site_id,
        "pillar_pages": pillar_urls,
        "all_pages": pillar_pages_list,
        "blog_posts": blog_posts,
        "categories": cat_list,
        "tags": tag_list,
    }, indent=2)


@mcp.tool()
def check_duplicate_topics(site_id: str, proposed_topic: str) -> str:
    """Check if a proposed blog topic already exists or is similar to posts from the past year.
    Returns matching/similar posts so the agent can decide whether to UPDATE an existing
    post instead of creating a new one.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
        proposed_topic: The topic or title of the blog post you want to write.
    """
    cfg = _require_site(site_id)
    s = wp_session_for_site(cfg)
    base = f"{cfg.wp_site_url}/wp-json/wp/v2"

    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00")

    search_results = s.get(f"{base}/posts", params={
        "search": proposed_topic,
        "after": one_year_ago,
        "per_page": 20,
        "status": "publish,future",
        "orderby": "relevance",
    }).json()

    keywords = [w for w in proposed_topic.lower().split() if len(w) >= 4
                and w not in {"what", "when", "where", "which", "that", "this",
                              "with", "from", "about", "have", "does", "your",
                              "their", "there", "been", "will", "more", "into"}]

    keyword_matches = []
    for kw in keywords[:3]:
        kw_results = s.get(f"{base}/posts", params={
            "search": kw,
            "after": one_year_ago,
            "per_page": 10,
            "status": "publish,future",
        }).json()
        keyword_matches.extend(kw_results)

    seen_ids = set()
    all_matches = []
    for post in search_results + keyword_matches:
        pid = post.get("id")
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        title = unescape(re.sub(r"<[^>]+>", "", post.get("title", {}).get("rendered", "")))
        all_matches.append({
            "wp_post_id": pid,
            "title": title,
            "url": post.get("link", ""),
            "date": post.get("date", ""),
            "slug": post.get("slug", ""),
            "status": post.get("status", ""),
        })

    _log_activity(site_id, "check_duplicate_topics",
                  f"topic='{proposed_topic}' matches={len(all_matches)}")

    return json.dumps({
        "site_id": site_id,
        "proposed_topic": proposed_topic,
        "matches_found": len(all_matches),
        "instruction": (
            "If any match is substantially similar to the proposed topic, "
            "use update_existing_post to refresh that post instead of creating a new one. "
            "Update with current information and strengthen internal links to pillar pages."
        ),
        "existing_posts": all_matches,
    }, indent=2)


@mcp.tool()
def update_existing_post(
    site_id: str,
    wp_post_id: int,
    title: str,
    content_html: str,
    slug: str = "",
    categories: list[str] | None = None,
    tags: list[str] | None = None,
    focus_keyphrase: str = "",
    seo_title: str = "",
    meta_description: str = "",
    excerpt: str = "",
    featured_image_query: str = "",
) -> str:
    """Update an existing WordPress post with refreshed content and internal links.
    Use this instead of publish_blog_post when check_duplicate_topics found a similar post.

    HARD RULES (same as publish_blog_post):
    - Minimum 1,000 words, author from site config, Yoast fields required, excerpt required.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
        wp_post_id: The WordPress post ID to update.
        title: Updated title.
        content_html: Full updated HTML content (include CTAs and internal links).
        slug: Optional new slug.
        categories: Optional updated category names.
        tags: Optional updated tag names.
        focus_keyphrase: Yoast focus keyphrase.
        seo_title: Yoast SEO title (30-60 chars, must contain focus keyphrase).
        meta_description: Yoast meta description (120-156 chars, must contain focus keyphrase).
        excerpt: Post excerpt (2-3 sentence summary).
        featured_image_query: Search term for a new featured image (realistic photo, no text).
    """
    cfg = _require_site(site_id)

    # -- Yoast SEO validation
    yoast_err = _validate_yoast(focus_keyphrase, seo_title, meta_description)
    if yoast_err:
        return json.dumps({"success": False, "error": f"Yoast SEO validation failed: {yoast_err}"}, indent=2)

    # -- Excerpt required
    if not excerpt or len(excerpt.strip()) < 10:
        return json.dumps({"success": False, "error": "Post excerpt is required. Provide a 2-3 sentence summary."}, indent=2)

    # -- Minimum word count
    word_count = _count_words_html(content_html)
    min_words = cfg.min_word_count or MIN_WORD_COUNT
    if word_count < min_words:
        return json.dumps({
            "success": False,
            "error": f"Article body is only {word_count} words. Minimum is {min_words}. Please expand the content and try again.",
            "word_count": word_count,
            "minimum_required": min_words,
        }, indent=2)

    s = wp_session_for_site(cfg)
    base = f"{cfg.wp_site_url}/wp-json/wp/v2"
    author_id = cfg.default_author_id

    payload: dict = {
        "title": title,
        "content": content_html,
        "author": author_id,
        "excerpt": excerpt,
        "meta": {
            "_yoast_wpseo_focuskw": focus_keyphrase,
            "_yoast_wpseo_title": seo_title,
            "_yoast_wpseo_metadesc": meta_description,
        },
    }
    if slug:
        payload["slug"] = slug

    # Resolve categories
    if categories:
        cat_ids = []
        for name in categories:
            existing = s.get(f"{base}/categories", params={"search": name}).json()
            if existing:
                cat_ids.append(existing[0]["id"])
            else:
                resp = s.post(f"{base}/categories", json={"name": name})
                if resp.ok:
                    cat_ids.append(resp.json()["id"])
        payload["categories"] = cat_ids

    # Resolve tags
    if tags:
        tag_ids = []
        for name in tags:
            existing = s.get(f"{base}/tags", params={"search": name}).json()
            if existing:
                tag_ids.append(existing[0]["id"])
            else:
                resp = s.post(f"{base}/tags", json={"name": name})
                if resp.ok:
                    tag_ids.append(resp.json()["id"])
        payload["tags"] = tag_ids

    # Featured image
    if featured_image_query:
        media_id = _wp_upload_featured_image(s, cfg.wp_site_url, featured_image_query)
        if media_id:
            payload["featured_media"] = media_id

    resp = s.post(f"{base}/posts/{wp_post_id}", json=payload)
    resp.raise_for_status()
    post = resp.json()

    # Save to published_posts with post_type='update'
    _save_post(wp_post_id, title, slug or post.get("slug", ""),
               post.get("date", ""), site_id, post_type="update", author_wp_id=author_id)

    # Extract internal links
    _extract_and_save_links(site_id, wp_post_id, content_html, cfg.pillar_pages or [])

    _log_activity(site_id, "update_existing_post",
                  f"wp_post_id={wp_post_id} title='{title}'")

    return json.dumps({
        "success": True,
        "action": "updated_existing",
        "site_id": site_id,
        "wp_post_id": wp_post_id,
        "title": title,
        "url": post.get("link", ""),
    }, indent=2)


@mcp.tool()
def get_next_publish_slots(site_id: str, count: int = 4) -> str:
    """Get the next available publish time slots for a site.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
        count: Number of slots to return (default 4).
    """
    cfg = _require_site(site_id)
    tz = ZoneInfo(cfg.timezone)
    now = datetime.now(tz)
    scheduled = _get_scheduled_times(site_id)
    publish_times = cfg.publish_times or ["09:00", "14:00"]
    slots = []

    for day_offset in range(30):
        day = now.date() + timedelta(days=day_offset)
        for time_str in publish_times:
            hour, minute = map(int, time_str.split(":"))
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            if slot <= now:
                continue
            slot_iso = slot.isoformat()
            if slot_iso not in scheduled:
                slots.append(slot_iso)
                if len(slots) >= count:
                    return json.dumps(slots, indent=2)

    return json.dumps(slots, indent=2)


@mcp.tool()
def publish_blog_post(
    site_id: str,
    title: str,
    content_html: str,
    slug: str,
    categories: list[str],
    tags: list[str],
    scheduled_time: str,
    focus_keyphrase: str = "",
    seo_title: str = "",
    meta_description: str = "",
    excerpt: str = "",
    featured_image_query: str = "",
) -> str:
    """Publish a blog post to a site's WordPress, scheduled for a future time.

    HARD RULES (automatically enforced):
    - Author is always set from site config (default_author_id).
    - A featured image is fetched from Unsplash (realistic photo, no text/symbols).
    - Posts matching pillar-page keywords get 2 CTA buttons with the site's phone number.
    - Minimum word count from site config (default 1,000).
    - BEFORE calling this, always call check_duplicate_topics first.
      If a similar post exists, use update_existing_post instead.
    - Yoast SEO fields are REQUIRED and must meet green-light criteria.
    - Post excerpt is REQUIRED.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
        title: Blog post title.
        content_html: Full HTML content with internal links to pillar pages.
        slug: URL slug (lowercase-hyphenated).
        categories: List of category names (e.g. ["Sober Living", "Austin"]).
        tags: List of tag names (e.g. ["sober-living-in-austin-texas"]).
        scheduled_time: ISO 8601 datetime for publication (from get_next_publish_slots).
        focus_keyphrase: Yoast focus keyphrase.
        seo_title: Yoast SEO title (30-60 chars, must contain focus keyphrase).
        meta_description: Yoast meta description (120-156 chars, must contain focus keyphrase).
        excerpt: Post excerpt (2-3 sentence summary of the article).
        featured_image_query: 2-4 word search term for realistic Unsplash photo (no text/symbols).
    """
    cfg = _require_site(site_id)

    # -- Yoast SEO validation
    yoast_err = _validate_yoast(focus_keyphrase, seo_title, meta_description)
    if yoast_err:
        return json.dumps({
            "success": False,
            "error": f"Yoast SEO validation failed: {yoast_err}",
            "tips": {
                "seo_title": f"Must be {YOAST_TITLE_MIN}-{YOAST_TITLE_MAX} chars and contain the focus keyphrase.",
                "meta_description": f"Must be {YOAST_DESC_MIN}-{YOAST_DESC_MAX} chars and contain the focus keyphrase.",
            },
        }, indent=2)

    # -- Excerpt required
    if not excerpt or len(excerpt.strip()) < 10:
        return json.dumps({
            "success": False,
            "error": "Post excerpt is required. Provide a 2-3 sentence summary.",
        }, indent=2)

    # -- Minimum word count
    min_words = cfg.min_word_count or MIN_WORD_COUNT
    word_count = _count_words_html(content_html)
    if word_count < min_words:
        return json.dumps({
            "success": False,
            "error": f"Article body is only {word_count} words. Minimum is {min_words}. Please expand the content and try again.",
            "word_count": word_count,
            "minimum_required": min_words,
        }, indent=2)

    s = wp_session_for_site(cfg)
    base = f"{cfg.wp_site_url}/wp-json/wp/v2"
    author_id = cfg.default_author_id
    cta_html = cfg.cta_html or ""
    pillar_pages = cfg.pillar_pages or []

    # -- Inject CTAs if the site has CTA HTML and content matches pillar keywords
    is_cta_relevant = False
    if cta_html:
        # Check if post relates to any pillar page keyword
        combined_text = (title + " " + slug).lower()
        for pp in pillar_pages:
            # Extract keywords from pillar page path
            pp_words = pp.strip("/").replace("-", " ").lower()
            if any(w in combined_text for w in pp_words.split() if len(w) >= 4):
                is_cta_relevant = True
                break

    if is_cta_relevant and cta_html:
        paragraphs = content_html.split("</p>")
        if len(paragraphs) > 3:
            insert_point = len(paragraphs) // 3
            paragraphs.insert(insert_point, f"</p>{cta_html}")
        content_html = "</p>".join(paragraphs) + cta_html

    # -- Resolve category IDs
    cat_ids = []
    for name in categories:
        existing = s.get(f"{base}/categories", params={"search": name}).json()
        if existing:
            cat_ids.append(existing[0]["id"])
        else:
            resp = s.post(f"{base}/categories", json={"name": name})
            if resp.ok:
                cat_ids.append(resp.json()["id"])

    # -- Resolve tag IDs
    tag_ids = []
    for name in tags:
        existing = s.get(f"{base}/tags", params={"search": name}).json()
        if existing:
            tag_ids.append(existing[0]["id"])
        else:
            resp = s.post(f"{base}/tags", json={"name": name})
            if resp.ok:
                tag_ids.append(resp.json()["id"])

    # -- Featured image from Unsplash
    featured_media_id = None
    if featured_image_query:
        featured_media_id = _wp_upload_featured_image(s, cfg.wp_site_url, featured_image_query)

    # -- Create scheduled post
    payload = {
        "title": title,
        "content": content_html,
        "slug": slug,
        "categories": cat_ids,
        "tags": tag_ids,
        "status": "future",
        "date": scheduled_time,
        "author": author_id,
        "excerpt": excerpt,
        "meta": {
            "_yoast_wpseo_focuskw": focus_keyphrase,
            "_yoast_wpseo_title": seo_title,
            "_yoast_wpseo_metadesc": meta_description,
        },
    }
    if featured_media_id:
        payload["featured_media"] = featured_media_id

    resp = s.post(f"{base}/posts", json=payload)
    resp.raise_for_status()
    post = resp.json()
    wp_id = post.get("id", 0)

    # Save with post_type='new'
    _save_post(wp_id, title, slug, scheduled_time, site_id,
               post_type="new", author_wp_id=author_id)

    # Extract internal links
    _extract_and_save_links(site_id, wp_id, content_html, pillar_pages)

    _log_activity(site_id, "publish_blog_post",
                  f"wp_post_id={wp_id} title='{title}' scheduled={scheduled_time}")

    return json.dumps({
        "success": True,
        "site_id": site_id,
        "wp_post_id": wp_id,
        "title": title,
        "url": post.get("link", ""),
        "scheduled_for": scheduled_time,
        "author_id": author_id,
        "featured_image": "set" if featured_media_id else "failed -- add UNSPLASH_ACCESS_KEY to .env",
        "ctas_injected": 2 if (is_cta_relevant and cta_html) else 0,
        "yoast_focus_keyphrase": focus_keyphrase,
        "yoast_seo_title": f"{seo_title} ({len(seo_title)} chars)",
        "yoast_meta_description": f"{meta_description} ({len(meta_description)} chars)",
        "excerpt": excerpt[:80] + "...",
    }, indent=2)


@mcp.tool()
def list_published_posts(site_id: str, limit: int = 20) -> str:
    """List blog posts that have been published or scheduled by this agent for a site.

    Args:
        site_id: The site identifier (e.g. 'eudaimonia', 'nova', 'briarwood').
        limit: Maximum number of posts to return (default 20).
    """
    _require_site(site_id)
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM published_posts WHERE site_id=? ORDER BY created_at DESC LIMIT ?",
        (site_id, limit),
    ).fetchall()
    conn.close()
    _log_activity(site_id, "list_published_posts", f"Returned {len(rows)} posts")
    return json.dumps([dict(r) for r in rows], indent=2)


if __name__ == "__main__":
    mcp.run()
