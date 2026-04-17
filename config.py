import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

# --- RingCentral ---
RC_CLIENT_ID = os.getenv("RC_CLIENT_ID")
RC_CLIENT_SECRET = os.getenv("RC_CLIENT_SECRET")
RC_JWT_TOKEN = os.getenv("RC_JWT_TOKEN")
RC_SERVER = os.getenv("RC_SERVER", "https://platform.ringcentral.com")

# --- Anthropic ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-6"

# --- WordPress ---
WP_SITE_URL = os.getenv("WP_SITE_URL", "https://eudaimoniahomes.com")
WP_USERNAME = os.getenv("WP_USERNAME")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

# --- Blog Settings ---
POSTS_PER_DAY = 2
PUBLISH_TIMES = ["09:00", "14:00"]  # Central Time
TIMEZONE = "America/Chicago"
MIN_WORD_COUNT = 1200
MAX_WORD_COUNT = 1800

# --- Austin Pillar Pages (always link to these) ---
AUSTIN_PILLAR_PAGES = [
    "/sober-living-austin-guide/",
    "/discover-quality-sober-living-options-in-austin-tx/",
    "/top-sober-homes-austin/",
    "/sober-living-in-austin-texas-recovery-and-college/",
]

# --- Default categories & tags for new posts ---
DEFAULT_CATEGORIES = ["Sober Living", "Austin"]
DEFAULT_TAGS = ["sober-living-in-austin-texas", "sober-living-homes"]

# --- How far back to fetch RingCentral calls (days) ---
RC_CALL_LOG_DAYS = 7

# --- SQLite DB path ---
DB_PATH = os.path.join(os.path.dirname(__file__), "db", "tracker.db")

# --- Site structure cache refresh interval (days) ---
SITE_CACHE_REFRESH_DAYS = 7
