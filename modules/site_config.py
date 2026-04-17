"""Site configuration loaded from SQLite sites table."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "tracker.db"


@dataclass
class SiteConfig:
    id: str
    name: str
    domain: str
    wp_site_url: str
    wp_username: str = ""
    wp_app_password: str = ""
    default_author_id: int = 0
    phone_number: str = ""
    cta_html: str = ""
    timezone: str = "America/Chicago"
    publish_times: list[str] = field(default_factory=lambda: ["09:00", "14:00"])
    min_word_count: int = 1000
    pillar_pages: list[str] = field(default_factory=list)
    default_categories: list[str] = field(default_factory=list)
    default_tags: list[str] = field(default_factory=list)
    ga4_property_id: str = ""
    gsc_site_url: str = ""
    brand_color: str = "#2c6e49"
    active: bool = True


def _parse_json_field(val: str | None, default=None):
    if not val:
        return default or []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default or []


def _row_to_config(row: sqlite3.Row) -> SiteConfig:
    return SiteConfig(
        id=row["id"],
        name=row["name"],
        domain=row["domain"],
        wp_site_url=row["wp_site_url"],
        wp_username=row["wp_username"] or "",
        wp_app_password=row["wp_app_password"] or "",
        default_author_id=row["default_author_id"] or 0,
        phone_number=row["phone_number"] or "",
        cta_html=row["cta_html"] or "",
        timezone=row["timezone"] or "America/Chicago",
        publish_times=_parse_json_field(row["publish_times"], ["09:00", "14:00"]),
        min_word_count=row["min_word_count"] or 1000,
        pillar_pages=_parse_json_field(row["pillar_pages"]),
        default_categories=_parse_json_field(row["default_categories"]),
        default_tags=_parse_json_field(row["default_tags"]),
        ga4_property_id=row["ga4_property_id"] or "",
        gsc_site_url=row["gsc_site_url"] or "",
        brand_color=row["brand_color"] or "#2c6e49",
        active=bool(row["active"]),
    )


def get_site(site_id: str) -> SiteConfig | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    conn.close()
    return _row_to_config(row) if row else None


def get_all_sites() -> list[SiteConfig]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM sites WHERE active = 1 ORDER BY name").fetchall()
    conn.close()
    return [_row_to_config(r) for r in rows]


def save_site(config: SiteConfig):
    conn = sqlite3.connect(str(DB_PATH))
    from datetime import datetime
    now = datetime.now().isoformat()
    conn.execute("""UPDATE sites SET
        name=?, domain=?, wp_site_url=?, wp_username=?, wp_app_password=?,
        default_author_id=?, phone_number=?, cta_html=?, timezone=?,
        publish_times=?, min_word_count=?, pillar_pages=?, default_categories=?,
        default_tags=?, ga4_property_id=?, gsc_site_url=?, brand_color=?,
        active=?, updated_at=?
        WHERE id=?""",
        (config.name, config.domain, config.wp_site_url, config.wp_username,
         config.wp_app_password, config.default_author_id, config.phone_number,
         config.cta_html, config.timezone, json.dumps(config.publish_times),
         config.min_word_count, json.dumps(config.pillar_pages),
         json.dumps(config.default_categories), json.dumps(config.default_tags),
         config.ga4_property_id, config.gsc_site_url, config.brand_color,
         int(config.active), now, config.id))
    conn.commit()
    conn.close()


def get_site_authors(site_id: str) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM site_authors WHERE site_id = ? ORDER BY is_default DESC, display_name",
        (site_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def wp_session_for_site(config: SiteConfig):
    """Create an authenticated requests.Session for a site's WordPress REST API.
    Handles both Basic Auth and token-based auth (for sites like Nova behind LiteSpeed).
    """
    import requests
    from requests.auth import HTTPBasicAuth

    s = requests.Session()
    s.headers.update({"User-Agent": "BlogAgent/2.0"})

    pw = config.wp_app_password
    if pw.startswith("token:"):
        # Token-based auth: append ?agent_token=xxx to every request
        token = pw.split(":", 1)[1]
        orig_get = s.get
        orig_post = s.post

        def _get_with_token(url, **kwargs):
            params = kwargs.pop("params", {}) or {}
            params["agent_token"] = token
            return orig_get(url, params=params, **kwargs)

        def _post_with_token(url, **kwargs):
            params = kwargs.pop("params", {}) or {}
            params["agent_token"] = token
            return orig_post(url, params=params, **kwargs)

        s.get = _get_with_token
        s.post = _post_with_token
    else:
        # Standard Basic Auth with Application Password
        s.auth = HTTPBasicAuth(config.wp_username, pw)

    return s


def get_site_by_phone(phone_number: str) -> str | None:
    """Look up site_id by phone number. Returns None if not found.

    Checks site_phone_numbers table first, then falls back to the
    phone_number field in the sites table.
    """
    if not phone_number:
        return None

    # Normalize: strip everything except digits
    digits = "".join(c for c in phone_number if c.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]  # take last 10 digits
    if not digits:
        return None

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Check site_phone_numbers table first
    try:
        rows = conn.execute("SELECT site_id, phone_number FROM site_phone_numbers").fetchall()
        for row in rows:
            row_digits = "".join(c for c in row["phone_number"] if c.isdigit())
            if len(row_digits) > 10:
                row_digits = row_digits[-10:]
            if row_digits == digits:
                conn.close()
                return row["site_id"]
    except Exception:
        pass  # Table might not exist or be empty

    # Fallback: check phone_number field in sites table
    try:
        site_rows = conn.execute("SELECT id, phone_number FROM sites WHERE phone_number IS NOT NULL AND phone_number != ''").fetchall()
        for row in site_rows:
            row_digits = "".join(c for c in row["phone_number"] if c.isdigit())
            if len(row_digits) > 10:
                row_digits = row_digits[-10:]
            if row_digits == digits:
                conn.close()
                return row["id"]
    except Exception:
        pass

    conn.close()
    return None
