"""Seed the 3 sites into the database.
Usage: python db/seed_sites.py
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "tracker.db"


def make_cta(phone: str, color: str) -> str:
    phone_digits = phone.replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
    return (
        f'<div style="text-align:center;margin:2em 0;">'
        f'<a href="tel:+1{phone_digits}" style="display:inline-block;background-color:{color};color:#ffffff;'
        f'padding:16px 32px;font-size:18px;font-weight:bold;text-decoration:none;border-radius:8px;">'
        f'Call Now: {phone}</a></div>'
    )


SITES = [
    {
        "id": "nova",
        "name": "Nova Recovery Center",
        "domain": "novarecoverycenter.com",
        "wp_site_url": "https://novarecoverycenter.com",
        "wp_username": "shehan",
        "wp_app_password": "token:nv_a8k2mP9xR4wL7qJ3",
        "default_author_id": 27,
        "phone_number": "(512) 209-6925",
        "cta_html": make_cta("(512) 209-6925", "#1a4d8f"),
        "timezone": "America/Chicago",
        "publish_times": json.dumps(["09:00", "14:00"]),
        "min_word_count": 1000,
        "pillar_pages": json.dumps([
            "/drug-alcohol-rehab-austin-tx/",
            "/outpatient-rehab/",
            "/inpatient-drug-rehab/",
        ]),
        "default_categories": json.dumps(["Addiction Treatment", "Recovery", "Austin"]),
        "default_tags": json.dumps(["drug-rehab-austin", "alcohol-rehab-austin", "outpatient-treatment"]),
        "ga4_property_id": "",
        "gsc_site_url": "https://novarecoverycenter.com",
        "brand_color": "#1a4d8f",
    },
    {
        "id": "briarwood",
        "name": "Briarwood Detox Center",
        "domain": "briarwooddetox.com",
        "wp_site_url": "https://briarwooddetox.com",
        "wp_username": "shehan",
        "wp_app_password": "MA0d L89h CaEx 141D MBRW gpyK",
        "default_author_id": 5,
        "phone_number": "(512) 262-4426",
        "cta_html": make_cta("(512) 262-4426", "#5b3a1a"),
        "timezone": "America/Chicago",
        "publish_times": json.dumps(["09:00", "14:00"]),
        "min_word_count": 1000,
        "pillar_pages": json.dumps([
            "/drug-detox-austin/",
            "/alcohol-detox-austin/",
            "/medical-detox/",
        ]),
        "default_categories": json.dumps(["Detox", "Recovery", "Austin"]),
        "default_tags": json.dumps(["drug-detox-austin", "alcohol-detox", "medical-detox"]),
        "ga4_property_id": "",
        "gsc_site_url": "https://briarwooddetox.com",
        "brand_color": "#5b3a1a",
    },
    {
        "id": "eudaimonia",
        "name": "Eudaimonia Recovery Homes",
        "domain": "eudaimoniahomes.com",
        "wp_site_url": "https://eudaimoniahomes.com",
        "wp_username": "shehan",
        "wp_app_password": "DS7g pM5l D4vF cl4I TOQg Eixx",
        "default_author_id": 53,
        "phone_number": "(512) 240-6612",
        "cta_html": make_cta("(512) 240-6612", "#2c6e49"),
        "timezone": "America/Chicago",
        "publish_times": json.dumps(["09:00", "14:00"]),
        "min_word_count": 1000,
        "pillar_pages": json.dumps([
            "/sober-living-austin-guide/",
            "/discover-quality-sober-living-options-in-austin-tx/",
            "/top-sober-homes-austin/",
            "/sober-living-in-austin-texas-recovery-and-college/",
        ]),
        "default_categories": json.dumps(["Sober Living", "Austin", "Recovery Resources"]),
        "default_tags": json.dumps(["sober-living-austin", "sober-living-homes", "recovery-austin"]),
        "ga4_property_id": "",
        "gsc_site_url": "https://eudaimoniahomes.com",
        "brand_color": "#2c6e49",
    },
]


def seed(conn: sqlite3.Connection):
    c = conn.cursor()
    now = datetime.now().isoformat()

    for site in SITES:
        c.execute("""INSERT OR REPLACE INTO sites
            (id, name, domain, wp_site_url, wp_username, wp_app_password,
             default_author_id, phone_number, cta_html, timezone, publish_times,
             min_word_count, pillar_pages, default_categories, default_tags,
             ga4_property_id, gsc_site_url, brand_color, active, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (site["id"], site["name"], site["domain"], site["wp_site_url"],
             site["wp_username"], site["wp_app_password"], site["default_author_id"],
             site["phone_number"], site["cta_html"], site["timezone"], site["publish_times"],
             site["min_word_count"], site["pillar_pages"], site["default_categories"],
             site["default_tags"], site["ga4_property_id"], site["gsc_site_url"],
             site["brand_color"], now, now))
        print(f"  Seeded: {site['name']} ({site['id']})")

    # Seed Eudaimonia author (Basil Ciocon — we know this one)
    c.execute("""INSERT OR IGNORE INTO site_authors (site_id, wp_user_id, display_name, is_default)
        VALUES ('eudaimonia', 53, 'Basil Ciocon', 1)""")

    conn.commit()
    print("Seed complete.")


if __name__ == "__main__":
    print(f"Seeding sites into: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    seed(conn)
    conn.close()
