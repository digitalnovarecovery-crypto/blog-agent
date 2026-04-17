"""
Direct MySQL bridge for WordPress post creation.

Bypasses the WP REST API entirely — used when the server cannot make
HTTP requests to its own domain (e.g., GoDaddy VPS + Wordfence).

Reads DB credentials from wp-config.php automatically.
"""
from __future__ import annotations

import json
import os
import re
import struct
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pymysql
import pymysql.cursors


def _parse_wp_config(wp_path: str) -> dict:
    """Extract DB credentials from wp-config.php."""
    config_file = os.path.join(wp_path, "wp-config.php")
    with open(config_file, "r") as f:
        content = f.read()

    result = {}
    for key in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST"):
        m = re.search(rf"define\(\s*'{key}'\s*,\s*'([^']*)'\s*\)", content)
        if m:
            result[key] = m.group(1)

    m = re.search(r"\$table_prefix\s*=\s*'([^']*)'", content)
    result["table_prefix"] = m.group(1) if m else "wp_"

    return result


class WPMySQLBridge:
    def __init__(self, wp_path: str = None, db_config: dict = None):
        """
        Initialize with either:
          - wp_path: path to WordPress root (reads wp-config.php)
          - db_config: dict with db_name, db_user, db_password, db_host, table_prefix
        """
        if db_config:
            self.db_name = db_config["db_name"]
            self.db_user = db_config["db_user"]
            self.db_password = db_config["db_password"]
            self.db_host = db_config.get("db_host", "localhost")
            self.prefix = db_config.get("table_prefix", "wp_")
            self.wp_path = db_config.get("wp_path", "")
        elif wp_path:
            cfg = _parse_wp_config(wp_path)
            self.db_name = cfg["DB_NAME"]
            self.db_user = cfg["DB_USER"]
            self.db_password = cfg["DB_PASSWORD"]
            self.db_host = cfg.get("DB_HOST", "localhost")
            self.prefix = cfg.get("table_prefix", "wp_")
            self.wp_path = wp_path
        else:
            raise ValueError("Must provide wp_path or db_config")

        self._site_url = None

    def _conn(self):
        return pymysql.connect(
            host=self.db_host,
            user=self.db_user,
            password=self.db_password,
            database=self.db_name,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _t(self, table: str) -> str:
        """Prefixed table name."""
        return f"{self.prefix}{table}"

    def get_site_url(self) -> str:
        if self._site_url:
            return self._site_url
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT option_value FROM {self._t('options')} WHERE option_name='siteurl'"
                )
                row = cur.fetchone()
                self._site_url = row["option_value"] if row else ""
        finally:
            conn.close()
        return self._site_url

    @staticmethod
    def _slugify(text: str) -> str:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        text = re.sub(r"[^\w\s-]", "", text.lower())
        return re.sub(r"[-\s]+", "-", text).strip("-")[:200]

    def _to_gmt(self, local_dt_str: str) -> str:
        """Convert local datetime string to GMT (assumes US Central = UTC-5)."""
        try:
            dt = datetime.strptime(local_dt_str, "%Y-%m-%d %H:%M:%S")
            gmt = dt + timedelta(hours=5)
            return gmt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return local_dt_str

    def ping(self) -> dict:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT option_value FROM {self._t('options')} WHERE option_name='blogname'"
                )
                name = cur.fetchone()
            return {"status": "ok", "site_name": name["option_value"] if name else ""}
        finally:
            conn.close()

    def get_structure(self, limit: int = 50) -> list[dict]:
        """Get published pages and posts for internal linking context."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT ID, post_title, post_name, post_type
                        FROM {self._t('posts')}
                        WHERE post_status='publish' AND post_type IN ('post','page')
                        ORDER BY post_date DESC LIMIT %s""",
                    (limit,),
                )
                rows = cur.fetchall()
            site_url = self.get_site_url()
            links = []
            for r in rows:
                slug = r["post_name"]
                if r["post_type"] == "page":
                    url = f"{site_url}/{slug}/"
                else:
                    url = f"{site_url}/{slug}/"
                links.append({
                    "title": r["post_title"],
                    "url": url,
                    "type": r["post_type"],
                })
            return links
        finally:
            conn.close()

    def check_duplicate(self, topic: str) -> dict:
        """Check if a similar topic already exists."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT ID, post_title FROM {self._t('posts')}
                        WHERE post_type='post'
                          AND post_status IN ('publish','future')
                          AND post_date > DATE_SUB(NOW(), INTERVAL 1 YEAR)
                        ORDER BY post_date DESC LIMIT 100""",
                )
                posts = cur.fetchall()

            topic_words = {w.lower() for w in topic.split() if len(w) >= 4}
            if not topic_words:
                return {"is_duplicate": False, "match_title": ""}

            for p in posts:
                title_words = {w.lower() for w in p["post_title"].split() if len(w) >= 4}
                if not title_words:
                    continue
                overlap = len(topic_words & title_words)
                if overlap / len(topic_words) >= 0.6:
                    return {
                        "is_duplicate": True,
                        "match_title": p["post_title"],
                        "match_id": p["ID"],
                    }

            return {"is_duplicate": False, "match_title": ""}
        finally:
            conn.close()

    def _resolve_term(self, name: str, taxonomy: str) -> int:
        """Get or create a term, return term_taxonomy_id."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Check if term exists
                cur.execute(
                    f"""SELECT t.term_id, tt.term_taxonomy_id
                        FROM {self._t('terms')} t
                        JOIN {self._t('term_taxonomy')} tt ON t.term_id = tt.term_id
                        WHERE t.name = %s AND tt.taxonomy = %s""",
                    (name, taxonomy),
                )
                row = cur.fetchone()
                if row:
                    return row["term_taxonomy_id"]

                # Create term
                slug = self._slugify(name)
                cur.execute(
                    f"INSERT INTO {self._t('terms')} (name, slug, term_group) VALUES (%s, %s, 0)",
                    (name, slug),
                )
                term_id = cur.lastrowid

                cur.execute(
                    f"""INSERT INTO {self._t('term_taxonomy')}
                        (term_id, taxonomy, description, parent, count)
                        VALUES (%s, %s, '', 0, 0)""",
                    (term_id, taxonomy),
                )
                tt_id = cur.lastrowid
                conn.commit()
                return tt_id
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Media / featured-image upload via filesystem + MySQL
    # ------------------------------------------------------------------

    def upload_media(self, image_bytes: bytes, filename: str,
                     mime_type: str = "image/jpeg",
                     alt_text: str = "", author_id: int = 27) -> Optional[int]:
        """
        Upload an image to wp-content/uploads and register it in MySQL.

        Returns the attachment post ID (use as featured_media), or None on failure.
        """
        if not self.wp_path:
            return None

        now = datetime.now()
        year_month = now.strftime("%Y/%m")
        uploads_dir = os.path.join(self.wp_path, "wp-content", "uploads", year_month)
        os.makedirs(uploads_dir, exist_ok=True)

        # De-duplicate filename
        base, ext = os.path.splitext(filename)
        dest = os.path.join(uploads_dir, filename)
        counter = 1
        while os.path.exists(dest):
            filename = f"{base}-{counter}{ext}"
            dest = os.path.join(uploads_dir, filename)
            counter += 1

        # Write file to disk
        with open(dest, "wb") as f:
            f.write(image_bytes)

        # Try to set web-server-friendly permissions
        try:
            os.chmod(dest, 0o644)
        except OSError:
            pass

        relative_path = f"{year_month}/{filename}"   # e.g. "2026/04/image.jpg"
        site_url = self.get_site_url()
        file_url = f"{site_url}/wp-content/uploads/{relative_path}"

        # Get image dimensions
        width, height = self._image_dimensions(image_bytes)

        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        now_gmt = self._to_gmt(now_str)

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Insert attachment record in wp_posts
                cur.execute(
                    f"""INSERT INTO {self._t('posts')} (
                        post_author, post_date, post_date_gmt,
                        post_content, post_title, post_excerpt,
                        post_status, comment_status, ping_status,
                        post_password, post_name, to_ping, pinged,
                        post_modified, post_modified_gmt,
                        post_content_filtered, post_parent,
                        guid, menu_order, post_type, post_mime_type, comment_count
                    ) VALUES (
                        %s, %s, %s,
                        '', %s, '',
                        'inherit', 'open', 'closed',
                        '', %s, '', '',
                        %s, %s,
                        '', 0,
                        %s, 0, 'attachment', %s, 0
                    )""",
                    (
                        author_id, now_str, now_gmt,
                        filename, self._slugify(base),
                        now_str, now_gmt,
                        file_url, mime_type,
                    ),
                )
                attach_id = cur.lastrowid
                conn.commit()

            # Set attachment meta
            meta = {
                "_wp_attached_file": relative_path,
            }
            if alt_text:
                meta["_wp_attachment_image_alt"] = alt_text

            # Build _wp_attachment_metadata (PHP-serialized)
            if width and height:
                metadata_serialized = self._serialize_attachment_metadata(
                    relative_path, width, height
                )
                meta["_wp_attachment_metadata"] = metadata_serialized

            self._set_post_meta(attach_id, meta)
            return attach_id

        except Exception as e:
            conn.rollback()
            print(f"  ERROR uploading media via MySQL bridge: {e}")
            return None
        finally:
            conn.close()

    @staticmethod
    def _image_dimensions(data: bytes) -> tuple:
        """Get (width, height) from JPEG/PNG/WebP bytes without PIL."""
        if not data:
            return (0, 0)
        # JPEG
        if data[:2] == b'\xff\xd8':
            i = 2
            while i < len(data) - 8:
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h = struct.unpack(">H", data[i+5:i+7])[0]
                    w = struct.unpack(">H", data[i+7:i+9])[0]
                    return (w, h)
                length = struct.unpack(">H", data[i+2:i+4])[0]
                i += 2 + length
        # PNG
        elif data[:8] == b'\x89PNG\r\n\x1a\n':
            w = struct.unpack(">I", data[16:20])[0]
            h = struct.unpack(">I", data[20:24])[0]
            return (w, h)
        # WebP
        elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            if data[12:16] == b'VP8 ':
                w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
                h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
                return (w, h)
            elif data[12:16] == b'VP8L':
                bits = struct.unpack("<I", data[21:25])[0]
                w = (bits & 0x3FFF) + 1
                h = ((bits >> 14) & 0x3FFF) + 1
                return (w, h)
        return (0, 0)

    @staticmethod
    def _serialize_attachment_metadata(file_path: str, width: int, height: int) -> str:
        """Build a minimal PHP-serialized _wp_attachment_metadata string."""
        # WordPress expects: a:5:{s:5:"width";i:W;s:6:"height";i:H;s:4:"file";s:L:"path";s:5:"sizes";a:0:{}s:10:"image_meta";a:0:{}}
        file_str = f's:{len(file_path)}:"{file_path}";'
        return (
            f'a:5:{{s:5:"width";i:{width};s:6:"height";i:{height};'
            f's:4:"file";{file_str}'
            f's:5:"sizes";a:0:{{}}'
            f's:10:"image_meta";a:0:{{}}}}'
        )

    def create_post(self, data: dict) -> dict:
        """
        Create a WordPress post directly in MySQL.

        data keys:
          - title (str)
          - content_html (str)
          - slug (str, optional)
          - excerpt (str, optional)
          - status (str): 'publish', 'future', 'draft'
          - date (str): 'YYYY-MM-DD HH:MM:SS' local time
          - author_id (int): WP user ID
          - categories (list[str]): category names
          - tags (list[str]): tag names
          - focus_keyphrase (str): Yoast focus keyphrase
          - seo_title (str): Yoast SEO title
          - meta_description (str): Yoast meta description
          - featured_media (int): attachment ID (optional)
        """
        title = data.get("title", "Untitled")
        slug = data.get("slug") or self._slugify(title)
        content = data.get("content_html", "")
        excerpt = data.get("excerpt", "")
        status = data.get("status", "future")
        post_date = data.get("date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        post_date_gmt = self._to_gmt(post_date)
        author_id = data.get("author_id", 27)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        now_gmt = self._to_gmt(now)

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Insert into wp_posts
                cur.execute(
                    f"""INSERT INTO {self._t('posts')} (
                        post_author, post_date, post_date_gmt,
                        post_content, post_title, post_excerpt,
                        post_status, comment_status, ping_status,
                        post_password, post_name, to_ping, pinged,
                        post_modified, post_modified_gmt,
                        post_content_filtered, post_parent,
                        guid, menu_order, post_type, post_mime_type, comment_count
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, 'open', 'open',
                        '', %s, '', '',
                        %s, %s,
                        '', 0,
                        '', 0, 'post', '', 0
                    )""",
                    (
                        author_id, post_date, post_date_gmt,
                        content, title, excerpt,
                        status, slug,
                        now, now_gmt,
                    ),
                )
                post_id = cur.lastrowid

                # Update GUID (WP uses ?p=ID format)
                site_url = self.get_site_url()
                guid = f"{site_url}/?p={post_id}"
                cur.execute(
                    f"UPDATE {self._t('posts')} SET guid=%s WHERE ID=%s",
                    (guid, post_id),
                )

                conn.commit()

            # Set categories
            for cat_name in (data.get("categories") or []):
                tt_id = self._resolve_term(cat_name, "category")
                self._add_term_relationship(post_id, tt_id)

            # If no categories, assign Uncategorized (term_taxonomy_id = 1 typically)
            if not data.get("categories"):
                self._add_term_relationship(post_id, 1)

            # Set tags
            for tag_name in (data.get("tags") or []):
                tt_id = self._resolve_term(tag_name, "post_tag")
                self._add_term_relationship(post_id, tt_id)

            # Set post meta (Yoast SEO fields)
            meta = {}
            if data.get("focus_keyphrase"):
                meta["_yoast_wpseo_focuskw"] = data["focus_keyphrase"]
            if data.get("seo_title"):
                meta["_yoast_wpseo_title"] = data["seo_title"]
            if data.get("meta_description"):
                meta["_yoast_wpseo_metadesc"] = data["meta_description"]
            if data.get("featured_media"):
                meta["_thumbnail_id"] = str(data["featured_media"])

            if meta:
                self._set_post_meta(post_id, meta)

            return {
                "id": post_id,
                "title": title,
                "slug": slug,
                "status": status,
                "link": f"{site_url}/{slug}/",
                "guid": guid,
            }
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _add_term_relationship(self, post_id: int, term_taxonomy_id: int):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT IGNORE INTO {self._t('term_relationships')}
                        (object_id, term_taxonomy_id, term_order)
                        VALUES (%s, %s, 0)""",
                    (post_id, term_taxonomy_id),
                )
                # Update count
                cur.execute(
                    f"""UPDATE {self._t('term_taxonomy')}
                        SET count = (
                            SELECT COUNT(*) FROM {self._t('term_relationships')}
                            WHERE term_taxonomy_id = %s
                        ) WHERE term_taxonomy_id = %s""",
                    (term_taxonomy_id, term_taxonomy_id),
                )
                conn.commit()
        finally:
            conn.close()

    def _set_post_meta(self, post_id: int, meta: dict):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for key, value in meta.items():
                    cur.execute(
                        f"""INSERT INTO {self._t('postmeta')} (post_id, meta_key, meta_value)
                            VALUES (%s, %s, %s)""",
                        (post_id, key, value),
                    )
                conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="WP MySQL Bridge")
    parser.add_argument("--wp-path", required=True, help="Path to WordPress root")
    parser.add_argument("--action", required=True, choices=["ping", "get_structure", "check_duplicate", "create_post"])
    parser.add_argument("--topic", help="Topic for duplicate check")
    parser.add_argument("--json", help="JSON data for create_post")
    args = parser.parse_args()

    bridge = WPMySQLBridge(wp_path=args.wp_path)

    if args.action == "ping":
        print(json.dumps(bridge.ping()))
    elif args.action == "get_structure":
        print(json.dumps(bridge.get_structure()))
    elif args.action == "check_duplicate":
        print(json.dumps(bridge.check_duplicate(args.topic or "")))
    elif args.action == "create_post":
        data = json.loads(args.json) if args.json else json.load(sys.stdin)
        print(json.dumps(bridge.create_post(data)))
