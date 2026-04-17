from __future__ import annotations
import requests
from requests.auth import HTTPBasicAuth

import config


class WordPressClient:
    def __init__(self):
        self.base_url = f"{config.WP_SITE_URL}/wp-json/wp/v2"
        self.auth = HTTPBasicAuth(config.WP_USERNAME, config.WP_APP_PASSWORD)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({"User-Agent": "EudaimoniaBlogAgent/1.0"})
        self._category_cache: dict[str, int] = {}
        self._tag_cache: dict[str, int] = {}

    def _get(self, endpoint: str, params: dict | None = None) -> list | dict:
        url = f"{self.base_url}/{endpoint}"
        all_items = []
        params = params or {}
        params.setdefault("per_page", 100)
        page = 1

        while True:
            params["page"] = page
            resp = self.session.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                all_items.extend(data)
                total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
                if page >= total_pages:
                    break
                page += 1
            else:
                return data

        return all_items

    # --- Posts ---

    def get_posts(self, **kwargs) -> list[dict]:
        return self._get("posts", kwargs)

    def create_post(self, title: str, content: str, slug: str,
                    category_ids: list[int], tag_ids: list[int],
                    status: str = "draft", date: str | None = None,
                    meta_description: str = "") -> dict:
        payload = {
            "title": title,
            "content": content,
            "slug": slug,
            "categories": category_ids,
            "tags": tag_ids,
            "status": status,
        }
        if date:
            payload["date"] = date
        if meta_description:
            payload["meta"] = {"_yoast_wpseo_metadesc": meta_description}

        resp = self.session.post(f"{self.base_url}/posts", json=payload)
        resp.raise_for_status()
        return resp.json()

    # --- Pages ---

    def get_pages(self, **kwargs) -> list[dict]:
        return self._get("pages", kwargs)

    # --- Categories ---

    def get_categories(self) -> list[dict]:
        cats = self._get("categories")
        self._category_cache = {c["name"]: c["id"] for c in cats}
        return cats

    def get_or_create_category(self, name: str) -> int:
        if not self._category_cache:
            self.get_categories()

        if name in self._category_cache:
            return self._category_cache[name]

        resp = self.session.post(
            f"{self.base_url}/categories", json={"name": name}
        )
        if resp.status_code == 400 and "term_exists" in resp.text:
            existing = resp.json().get("data", {}).get("term_id")
            if existing:
                self._category_cache[name] = existing
                return existing
        resp.raise_for_status()
        cat = resp.json()
        self._category_cache[name] = cat["id"]
        return cat["id"]

    # --- Tags ---

    def get_tags(self) -> list[dict]:
        tags = self._get("tags")
        self._tag_cache = {t["name"]: t["id"] for t in tags}
        return tags

    def get_or_create_tag(self, name: str) -> int:
        if not self._tag_cache:
            self.get_tags()

        if name in self._tag_cache:
            return self._tag_cache[name]

        resp = self.session.post(
            f"{self.base_url}/tags", json={"name": name}
        )
        if resp.status_code == 400 and "term_exists" in resp.text:
            existing = resp.json().get("data", {}).get("term_id")
            if existing:
                self._tag_cache[name] = existing
                return existing
        resp.raise_for_status()
        tag = resp.json()
        self._tag_cache[name] = tag["id"]
        return tag["id"]

    def resolve_categories(self, names: list[str]) -> list[int]:
        return [self.get_or_create_category(n) for n in names]

    def resolve_tags(self, names: list[str]) -> list[int]:
        return [self.get_or_create_tag(n) for n in names]

    def test_connection(self) -> bool:
        try:
            # Try /users/me first; some security plugins block it, so fall back to /posts
            resp = self.session.get(f"{self.base_url}/users/me", params={"context": "edit"})
            if resp.status_code == 200:
                user = resp.json()
                print(f"Connected to WordPress as: {user.get('name', 'unknown')}")
                return True

            # Fallback: try listing posts (proves auth works)
            resp = self.session.get(f"{self.base_url}/posts", params={"per_page": 1})
            resp.raise_for_status()
            posts = resp.json()
            print(f"Connected to WordPress (verified via posts endpoint, {len(posts)} post returned)")
            return True
        except Exception as e:
            print(f"WordPress connection failed: {e}")
            return False
