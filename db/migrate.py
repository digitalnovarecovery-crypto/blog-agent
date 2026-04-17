"""Database migration script — run once to upgrade schema for multi-site support.
Usage: python db/migrate.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "tracker.db"


def migrate(conn: sqlite3.Connection):
    c = conn.cursor()

    # ── New tables ────────────────────────────────────────────────────────

    c.execute("""CREATE TABLE IF NOT EXISTS sites (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        domain TEXT NOT NULL,
        wp_site_url TEXT NOT NULL,
        wp_username TEXT NOT NULL DEFAULT '',
        wp_app_password TEXT NOT NULL DEFAULT '',
        default_author_id INTEGER DEFAULT 0,
        phone_number TEXT DEFAULT '',
        cta_html TEXT DEFAULT '',
        timezone TEXT DEFAULT 'America/Chicago',
        publish_times TEXT DEFAULT '["09:00","14:00"]',
        min_word_count INTEGER DEFAULT 1000,
        pillar_pages TEXT DEFAULT '[]',
        default_categories TEXT DEFAULT '[]',
        default_tags TEXT DEFAULT '[]',
        ga4_property_id TEXT DEFAULT '',
        gsc_site_url TEXT DEFAULT '',
        brand_color TEXT DEFAULT '#2c6e49',
        active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS site_authors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT NOT NULL,
        wp_user_id INTEGER NOT NULL,
        display_name TEXT DEFAULT '',
        email TEXT DEFAULT '',
        is_default INTEGER DEFAULT 0,
        FOREIGN KEY (site_id) REFERENCES sites(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS site_phone_numbers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT NOT NULL,
        phone_number TEXT NOT NULL,
        label TEXT DEFAULT '',
        FOREIGN KEY (site_id) REFERENCES sites(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS traffic_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT NOT NULL,
        wp_post_id INTEGER,
        url TEXT NOT NULL,
        date TEXT NOT NULL,
        pageviews INTEGER DEFAULT 0,
        sessions INTEGER DEFAULT 0,
        avg_time_on_page REAL DEFAULT 0,
        bounce_rate REAL DEFAULT 0,
        impressions INTEGER DEFAULT 0,
        clicks INTEGER DEFAULT 0,
        ctr REAL DEFAULT 0,
        avg_position REAL DEFAULT 0,
        fetched_at TEXT,
        UNIQUE(site_id, url, date),
        FOREIGN KEY (site_id) REFERENCES sites(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS post_internal_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT NOT NULL,
        wp_post_id INTEGER NOT NULL,
        target_url TEXT NOT NULL,
        anchor_text TEXT DEFAULT '',
        is_pillar INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY (site_id) REFERENCES sites(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS agent_activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT,
        action TEXT NOT NULL,
        details TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")

    # ── Add site_id to existing tables (safe: ignores if column exists) ──

    existing_tables = {
        "processed_calls": ["site_id TEXT DEFAULT 'eudaimonia'"],
        "extracted_questions": ["site_id TEXT DEFAULT 'eudaimonia'"],
        "published_posts": [
            "site_id TEXT DEFAULT 'eudaimonia'",
            "post_type TEXT DEFAULT 'new'",
            "source_transcript TEXT DEFAULT ''",
            "author_wp_id INTEGER DEFAULT 0",
        ],
        "internal_links": ["site_id TEXT DEFAULT 'eudaimonia'"],
    }

    for table, columns in existing_tables.items():
        # Check if table exists first
        exists = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        # Get existing columns
        existing_cols = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
        for col_def in columns:
            col_name = col_def.split()[0]
            if col_name not in existing_cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                print(f"  Added {col_name} to {table}")

    conn.commit()
    print("Migration complete.")


if __name__ == "__main__":
    print(f"Migrating database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    migrate(conn)
    conn.close()
