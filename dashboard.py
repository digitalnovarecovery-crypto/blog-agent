"""Multi-Site Blog Agent Dashboard
Run: python dashboard.py -> http://localhost:5000
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import functools

import requests
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import (
    Flask, flash, jsonify, redirect, render_template, request,
    session, url_for,
)
from requests.auth import HTTPBasicAuth
from urllib.parse import quote_plus, urlencode

import os
import sys

# allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))
from modules.site_config import (
    SiteConfig, get_site, get_all_sites, save_site,
    wp_session_for_site, get_site_authors,
)

# -- Config -------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
DB_PATH = Path(__file__).resolve().parent / "db" / "tracker.db"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev-secret-change-in-production")

DEFAULT_SITE = "eudaimonia"

# -- Auth0 --------------------------------------------------------------------
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "")
AUTH0_ENABLED = bool(AUTH0_DOMAIN and AUTH0_CLIENT_ID)

oauth = OAuth(app)
if AUTH0_ENABLED:
    oauth.register(
        "auth0",
        client_id=AUTH0_CLIENT_ID,
        client_secret=AUTH0_CLIENT_SECRET,
        client_kwargs={"scope": "openid profile email"},
        server_metadata_url=f"https://{AUTH0_DOMAIN}/.well-known/openid-configuration",
    )


def login_required(f):
    """Decorator: redirect to /login if not authenticated (skipped in dev mode)."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not AUTH0_ENABLED:
            return f(*args, **kwargs)  # dev mode — no auth
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# -- WP cache -----------------------------------------------------------------
_wp_cache: dict = {}
_wp_cache_ttl = 60  # seconds


def current_site_id() -> str:
    return session.get("current_site_id", DEFAULT_SITE)


def current_site() -> SiteConfig | None:
    return get_site(current_site_id())


def _wp_session_current():
    cfg = current_site()
    if not cfg:
        s = requests.Session()
        s.headers.update({"User-Agent": "BlogAgent/2.0"})
        return s, ""
    return wp_session_for_site(cfg), cfg.wp_site_url


def wp_get_post(wp_post_id: int) -> dict | None:
    cache_key = f"post_{current_site_id()}_{wp_post_id}"
    if cache_key in _wp_cache:
        cached, ts = _wp_cache[cache_key]
        if time.time() - ts < _wp_cache_ttl:
            return cached
    try:
        s, base = _wp_session_current()
        r = s.get(f"{base}/wp-json/wp/v2/posts/{wp_post_id}", params={"context": "edit"})
        if r.ok:
            data = r.json()
            _wp_cache[cache_key] = (data, time.time())
            return data
    except Exception:
        pass
    return None


def wp_get_posts_bulk(post_ids: list[int]) -> dict[int, dict]:
    if not post_ids:
        return {}
    results: dict[int, dict] = {}
    try:
        s, base = _wp_session_current()
        ids_str = ",".join(str(i) for i in post_ids[:20])
        r = s.get(f"{base}/wp-json/wp/v2/posts", params={
            "include": ids_str, "context": "edit", "per_page": 20,
            "status": "publish,future,draft,pending,private",
        })
        if r.ok:
            for p in r.json():
                results[p["id"]] = p
                _wp_cache[f"post_{current_site_id()}_{p['id']}"] = (p, time.time())
    except Exception:
        pass
    return results


def wp_update_post(wp_post_id: int, data: dict) -> bool:
    try:
        s, base = _wp_session_current()
        r = s.post(f"{base}/wp-json/wp/v2/posts/{wp_post_id}", json=data)
        if r.ok:
            _wp_cache.pop(f"post_{current_site_id()}_{wp_post_id}", None)
            return True
    except Exception:
        pass
    return False


def wp_test_connection(cfg: SiteConfig) -> tuple[bool, str]:
    """Test WP REST API connectivity; returns (ok, message)."""
    try:
        s = wp_session_for_site(cfg)
        r = s.get(f"{cfg.wp_site_url}/wp-json/wp/v2/users/me", timeout=10)
        if r.ok:
            name = r.json().get("name", "unknown")
            return True, f"Connected as {name}"
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as exc:
        return False, str(exc)[:200]


# -- DB helpers ---------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def query_db(sql, args=(), one=False):
    conn = get_db()
    try:
        rows = conn.execute(sql, args).fetchall()
        return (rows[0] if rows else None) if one else rows
    finally:
        conn.close()


def execute_db(sql, args=()):
    conn = get_db()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def site_query(sql, extra_args=(), one=False):
    """Convenience: injects current site_id as first parameter."""
    return query_db(sql, (current_site_id(), *extra_args), one=one)


# -- Jinja helpers ------------------------------------------------------------
def strip_html(html: str) -> str:
    import re
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", " ", html or "")).strip()


def format_dt(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return iso_str[:19]


app.jinja_env.filters["format_dt"] = format_dt
app.jinja_env.filters["strip_html"] = strip_html


# -- Context processor: inject site info into every template ------------------
@app.context_processor
def inject_site_globals():
    cfg = current_site()
    all_sites = get_all_sites()
    user = session.get("user", {})
    return dict(
        current_site=cfg,
        current_site_id=current_site_id(),
        all_sites=all_sites,
        brand_color=cfg.brand_color if cfg else "#2c6e49",
        site_name=cfg.name if cfg else "Dashboard",
        auth_user=user,
        auth_enabled=AUTH0_ENABLED,
    )


# -- Auth: before_request guard -----------------------------------------------
@app.before_request
def require_login():
    """Redirect to login for all pages except auth routes and static files."""
    if not AUTH0_ENABLED:
        return None
    public_routes = {"login", "callback", "logout", "static",
                      "api_health", "api_cron_run", "api_cron_status"}
    if request.endpoint in public_routes:
        return None
    # Also allow any /api/ path (belt-and-suspenders)
    if request.path.startswith("/api/"):
        return None
    if "user" not in session:
        return redirect(url_for("login"))
    return None


# -- Routes: Auth0 -----------------------------------------------------------
@app.route("/login")
def login():
    if not AUTH0_ENABLED:
        return redirect(url_for("home"))
    if request.args.get("prompt") == "none":
        # Direct Auth0 redirect
        return oauth.auth0.authorize_redirect(
            redirect_uri=url_for("callback", _external=True)
        )
    return render_template("login.html")


@app.route("/callback")
def callback():
    if not AUTH0_ENABLED:
        return redirect(url_for("home"))
    token = oauth.auth0.authorize_access_token()
    session["user"] = token.get("userinfo", {})
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    if not AUTH0_ENABLED:
        return redirect(url_for("home"))
    return redirect(
        f"https://{AUTH0_DOMAIN}/v2/logout?"
        + urlencode(
            {"returnTo": url_for("home", _external=True), "client_id": AUTH0_CLIENT_ID},
            quote_via=quote_plus,
        )
    )


# -- Routes: site switcher ---------------------------------------------------
@app.route("/switch-site/<site_id>")
def switch_site(site_id):
    cfg = get_site(site_id)
    if cfg:
        session["current_site_id"] = site_id
        _wp_cache.clear()
        flash(f"Switched to {cfg.name}.", "success")
    else:
        flash("Unknown site.", "error")
    return redirect(request.referrer or url_for("home"))


# -- Routes: dashboard -------------------------------------------------------
@app.route("/")
def home():
    sid = current_site_id()
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    month_ago = (datetime.now() - timedelta(days=30)).isoformat()

    posts_week = site_query(
        "SELECT COUNT(*) as c FROM published_posts WHERE site_id=? AND created_at >= ?",
        (week_ago,), one=True)
    posts_month = site_query(
        "SELECT COUNT(*) as c FROM published_posts WHERE site_id=? AND created_at >= ?",
        (month_ago,), one=True)
    total_calls = site_query(
        "SELECT COUNT(*) as c FROM processed_calls WHERE site_id=?", one=True)

    pending_q = 0
    try:
        row = site_query(
            "SELECT COUNT(*) as c FROM extracted_questions WHERE site_id=? AND status='pending'",
            one=True)
        pending_q = row["c"] if row else 0
    except Exception:
        pass

    recent_posts = site_query(
        "SELECT * FROM published_posts WHERE site_id=? ORDER BY created_at DESC LIMIT 5")
    recent_questions = []
    try:
        recent_questions = site_query(
            "SELECT * FROM extracted_questions WHERE site_id=? ORDER BY created_at DESC LIMIT 5")
    except Exception:
        pass

    # Top posts (most recent published)
    top_posts = site_query(
        "SELECT * FROM published_posts WHERE site_id=? ORDER BY created_at DESC LIMIT 10")

    wp_ids = [r["wp_post_id"] for r in recent_posts if r["wp_post_id"]]
    top_wp_ids = [r["wp_post_id"] for r in top_posts if r["wp_post_id"]]
    all_wp_ids = list(set(wp_ids + top_wp_ids))
    wp_data = wp_get_posts_bulk(all_wp_ids)

    return render_template("dashboard.html",
        posts_week=posts_week["c"] if posts_week else 0,
        posts_month=posts_month["c"] if posts_month else 0,
        total_calls=total_calls["c"] if total_calls else 0,
        pending_questions=pending_q,
        recent_posts=recent_posts,
        recent_questions=recent_questions,
        top_posts=top_posts,
        wp_data=wp_data,
    )


# -- Routes: posts ------------------------------------------------------------
@app.route("/posts")
def posts_list():
    status_filter = request.args.get("status", "all")
    all_posts = site_query(
        "SELECT * FROM published_posts WHERE site_id=? ORDER BY scheduled_time DESC")
    wp_ids = [r["wp_post_id"] for r in all_posts if r["wp_post_id"]]
    wp_data = wp_get_posts_bulk(wp_ids)

    filtered = []
    for p in all_posts:
        wp = wp_data.get(p["wp_post_id"], {})
        wp_status = wp.get("status", "unknown")
        if status_filter == "all" or wp_status == status_filter:
            filtered.append(p)

    # load authors for display
    authors = {}
    try:
        for a in get_site_authors(current_site_id()):
            authors[a["wp_author_id"]] = a["display_name"]
    except Exception:
        pass

    return render_template("posts.html",
        posts=filtered, wp_data=wp_data,
        status_filter=status_filter, authors=authors)


@app.route("/posts/<int:post_id>", methods=["GET"])
def post_edit(post_id):
    local = query_db(
        "SELECT * FROM published_posts WHERE wp_post_id = ? AND site_id = ?",
        (post_id, current_site_id()), one=True)
    wp = wp_get_post(post_id)
    if not wp:
        flash("Could not fetch post from WordPress.", "error")
        return redirect(url_for("posts_list"))

    authors = get_site_authors(current_site_id())

    # internal links for this post
    internal_links = []
    try:
        internal_links = query_db(
            "SELECT * FROM post_internal_links WHERE post_id = ? AND site_id = ?",
            (post_id, current_site_id()))
    except Exception:
        pass

    # source transcript
    source_transcript = ""
    if local and local["source_transcript"]:
        source_transcript = local["source_transcript"]

    return render_template("post_edit.html",
        local=local, wp=wp, post_id=post_id,
        authors=authors, internal_links=internal_links,
        source_transcript=source_transcript)


@app.route("/posts/<int:post_id>", methods=["POST"])
def post_save(post_id):
    data = {}
    title = request.form.get("title")
    if title:
        data["title"] = title
    slug = request.form.get("slug")
    if slug:
        data["slug"] = slug
    scheduled = request.form.get("scheduled_time")
    if scheduled:
        data["date"] = scheduled
        data["status"] = "future"
    excerpt = request.form.get("excerpt")
    if excerpt:
        data["excerpt"] = excerpt
    author_id = request.form.get("author_wp_id")
    if author_id:
        data["author"] = int(author_id)

    meta = {}
    fk = request.form.get("focus_keyphrase")
    if fk:
        meta["_yoast_wpseo_focuskw"] = fk
    st = request.form.get("seo_title")
    if st:
        meta["_yoast_wpseo_title"] = st
    md = request.form.get("meta_description")
    if md:
        meta["_yoast_wpseo_metadesc"] = md
    if meta:
        data["meta"] = meta

    if data:
        ok = wp_update_post(post_id, data)
        if ok:
            updates = []
            params = []
            if title:
                updates.append("title=?")
                params.append(title)
            if scheduled:
                updates.append("scheduled_time=?")
                params.append(scheduled)
            if author_id:
                updates.append("author_wp_id=?")
                params.append(int(author_id))
            if updates:
                params.extend([post_id, current_site_id()])
                execute_db(
                    f"UPDATE published_posts SET {', '.join(updates)} WHERE wp_post_id=? AND site_id=?",
                    tuple(params))
            flash("Post updated successfully.", "success")
        else:
            flash("Failed to update post on WordPress.", "error")

    return redirect(url_for("post_edit", post_id=post_id))


# -- Routes: calls ------------------------------------------------------------
@app.route("/calls")
def calls_list():
    calls = site_query(
        "SELECT * FROM processed_calls WHERE site_id=? ORDER BY processed_at DESC")

    # also get unassigned calls (site_id IS NULL)
    unassigned = query_db(
        "SELECT * FROM processed_calls WHERE site_id IS NULL OR site_id = '' ORDER BY processed_at DESC")

    all_sites = get_all_sites()
    return render_template("calls.html",
        calls=calls, unassigned=unassigned, all_sites=all_sites)


# -- Routes: questions --------------------------------------------------------
@app.route("/questions")
def questions_list():
    status = request.args.get("status", "all")
    if status == "all":
        questions = site_query(
            "SELECT * FROM extracted_questions WHERE site_id=? ORDER BY created_at DESC")
    else:
        questions = site_query(
            "SELECT * FROM extracted_questions WHERE site_id=? AND status = ? ORDER BY created_at DESC",
            (status,))
    return render_template("questions.html", questions=questions, status_filter=status)


# -- Routes: links ------------------------------------------------------------
@app.route("/links")
def links_list():
    links = []
    try:
        links = site_query(
            "SELECT * FROM internal_links WHERE site_id=? ORDER BY link_type, title")
    except Exception:
        pass

    grouped: dict[str, list] = {}
    for link in links:
        lt = link["link_type"] or "other"
        grouped.setdefault(lt, []).append(link)
    counts = {k: len(v) for k, v in grouped.items()}

    cfg = current_site()
    pillar_pages = cfg.pillar_pages if cfg else []

    return render_template("links.html",
        grouped=grouped, counts=counts, pillar_pages=pillar_pages)


# -- Routes: settings ---------------------------------------------------------
@app.route("/settings")
def settings_page():
    all_sites = get_all_sites()
    return render_template("settings.html", sites=all_sites)


@app.route("/settings/<site_id>", methods=["POST"])
def settings_save(site_id):
    cfg = get_site(site_id)
    if not cfg:
        flash("Site not found.", "error")
        return redirect(url_for("settings_page"))

    cfg.name = request.form.get("name", cfg.name)
    cfg.domain = request.form.get("domain", cfg.domain)
    cfg.wp_site_url = request.form.get("wp_site_url", cfg.wp_site_url)
    cfg.wp_username = request.form.get("wp_username", cfg.wp_username)
    pw = request.form.get("wp_app_password", "").strip()
    if pw:
        cfg.wp_app_password = pw
    cfg.phone_number = request.form.get("phone_number", cfg.phone_number)
    cfg.brand_color = request.form.get("brand_color", cfg.brand_color)
    cfg.timezone = request.form.get("timezone", cfg.timezone)
    cfg.min_word_count = int(request.form.get("min_word_count", cfg.min_word_count) or 1000)
    cfg.ga4_property_id = request.form.get("ga4_property_id", cfg.ga4_property_id)
    cfg.gsc_site_url = request.form.get("gsc_site_url", cfg.gsc_site_url)

    save_site(cfg)
    flash(f"Settings for {cfg.name} saved.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/<site_id>/test-wp", methods=["POST"])
def settings_test_wp(site_id):
    cfg = get_site(site_id)
    if not cfg:
        return jsonify({"ok": False, "message": "Site not found"})
    ok, msg = wp_test_connection(cfg)
    return jsonify({"ok": ok, "message": msg})


# -- Routes: activity log -----------------------------------------------------
@app.route("/activity")
def activity_log():
    page = int(request.args.get("page", 1))
    per_page = 50
    offset = (page - 1) * per_page
    action_filter = request.args.get("action", "all")

    if action_filter == "all":
        logs = site_query(
            "SELECT * FROM agent_activity_log WHERE site_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, offset))
        total_row = site_query(
            "SELECT COUNT(*) as c FROM agent_activity_log WHERE site_id=?", one=True)
    else:
        logs = site_query(
            "SELECT * FROM agent_activity_log WHERE site_id=? AND action=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (action_filter, per_page, offset))
        total_row = site_query(
            "SELECT COUNT(*) as c FROM agent_activity_log WHERE site_id=? AND action=?",
            (action_filter,), one=True)

    total = total_row["c"] if total_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page)

    # get distinct actions for filter dropdown
    actions = []
    try:
        actions = site_query(
            "SELECT DISTINCT action FROM agent_activity_log WHERE site_id=? ORDER BY action")
    except Exception:
        pass

    return render_template("activity_log.html",
        logs=logs, page=page, total_pages=total_pages,
        action_filter=action_filter,
        actions=[a["action"] for a in actions])


# -- Routes: calendar ---------------------------------------------------------
@app.route("/calendar")
def calendar_view():
    year = int(request.args.get("year", datetime.now().year))
    month = int(request.args.get("month", datetime.now().month))

    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    posts = site_query(
        "SELECT * FROM published_posts WHERE site_id=? AND scheduled_time >= ? AND scheduled_time < ? ORDER BY scheduled_time",
        (start.isoformat(), end.isoformat()))

    # Build calendar data: dict of day_number -> list of posts
    import calendar
    cal = calendar.Calendar(firstweekday=6)  # Sunday start
    weeks = cal.monthdayscalendar(year, month)

    posts_by_day: dict[int, list] = {}
    for p in posts:
        try:
            dt = datetime.fromisoformat(p["scheduled_time"].replace("Z", "+00:00"))
            posts_by_day.setdefault(dt.day, []).append(p)
        except Exception:
            pass

    prev_month = month - 1
    prev_year = year
    if prev_month < 1:
        prev_month = 12
        prev_year = year - 1
    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year = year + 1

    month_name = start.strftime("%B %Y")

    return render_template("calendar.html",
        weeks=weeks, posts_by_day=posts_by_day,
        year=year, month=month, month_name=month_name,
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month)


# -- API routes ---------------------------------------------------------------
@app.route("/api/posts/<int:post_id>/reschedule", methods=["POST"])
def api_reschedule(post_id):
    new_time = request.json.get("new_time")
    if not new_time:
        return jsonify({"error": "new_time required"}), 400
    ok = wp_update_post(post_id, {"date": new_time, "status": "future"})
    if ok:
        execute_db(
            "UPDATE published_posts SET scheduled_time=? WHERE wp_post_id=? AND site_id=?",
            (new_time, post_id, current_site_id()))
        return jsonify({"success": True})
    return jsonify({"error": "WordPress update failed"}), 500


@app.route("/api/questions/<int:q_id>/status", methods=["POST"])
def api_question_status(q_id):
    new_status = request.json.get("status")
    if new_status not in ("pending", "used", "archived"):
        return jsonify({"error": "Invalid status"}), 400
    execute_db(
        "UPDATE extracted_questions SET status=? WHERE id=? AND site_id=?",
        (new_status, q_id, current_site_id()))
    return jsonify({"success": True})


@app.route("/api/calls/<call_id>/assign", methods=["POST"])
def api_call_assign(call_id):
    site_id = request.json.get("site_id")
    if not site_id:
        return jsonify({"error": "site_id required"}), 400
    cfg = get_site(site_id)
    if not cfg:
        return jsonify({"error": "Unknown site"}), 400
    execute_db(
        "UPDATE processed_calls SET site_id=? WHERE call_id=?",
        (site_id, call_id))
    return jsonify({"success": True, "site_name": cfg.name})


@app.route("/api/refresh-wp", methods=["POST"])
def api_refresh():
    _wp_cache.clear()
    return jsonify({"success": True, "message": "Cache cleared"})


# -- Cron / Pipeline Trigger ---------------------------------------------------

import subprocess
import threading

_pipeline_lock = threading.Lock()
_pipeline_status = {"running": False, "last_run": None, "last_result": None}


@app.route("/api/cron/run", methods=["POST"])
def api_cron_run():
    """Trigger a pipeline run. Protected by CRON_SECRET."""
    secret = request.headers.get("X-Cron-Secret") or request.args.get("secret")
    expected = os.getenv("CRON_SECRET", "")
    if expected and secret != expected:
        return jsonify({"error": "Unauthorized"}), 401

    if _pipeline_status["running"]:
        return jsonify({"error": "Pipeline already running"}), 409

    # Set running=True BEFORE spawning thread to avoid race conditions
    _pipeline_status["running"] = True
    _pipeline_status["last_run"] = datetime.now().isoformat()

    def _run():
        import traceback
        try:
            result = subprocess.run(
                [sys.executable, "pipeline_runner.py"],
                capture_output=True, text=True, timeout=900,
                cwd=str(Path(__file__).resolve().parent),
                env={**os.environ},  # inherit all env vars
            )
            _pipeline_status["last_result"] = {
                "exit_code": result.returncode,
                "stdout_tail": result.stdout[-2000:] if result.stdout else "",
                "stderr_tail": result.stderr[-1000:] if result.stderr else "",
                "finished": datetime.now().isoformat(),
            }
        except subprocess.TimeoutExpired:
            _pipeline_status["last_result"] = {
                "exit_code": -1,
                "error": "Pipeline timed out after 15 minutes",
                "finished": datetime.now().isoformat(),
            }
        except Exception as e:
            _pipeline_status["last_result"] = {
                "exit_code": -1,
                "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
                "finished": datetime.now().isoformat(),
            }
        finally:
            _pipeline_status["running"] = False

    with _pipeline_lock:
        thread = threading.Thread(target=_run)
        thread.start()

    return jsonify({"success": True, "message": "Pipeline run started"})


@app.route("/api/cron/status")
def api_cron_status():
    """Get the current pipeline run status."""
    return jsonify(_pipeline_status)


@app.route("/api/health")
def api_health():
    """Health check endpoint for Railway."""
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@app.route("/api/debug/test-subprocess")
def api_debug_test():
    """Quick test: run a trivial subprocess to verify threading works."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import pipeline_runner; print('Pipeline module OK')"],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parent),
            env={**os.environ},
        )
        return jsonify({
            "exit_code": result.returncode,
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -- Scheduled daily pipeline run (6 AM CT / 11:00 UTC) ----------------------
def _scheduled_pipeline_run():
    """Called by APScheduler at 6 AM CT daily."""
    import logging
    logging.getLogger("apscheduler").info("Scheduled pipeline run starting...")
    if _pipeline_status["running"]:
        logging.getLogger("apscheduler").warning("Pipeline already running, skipping scheduled run")
        return
    _pipeline_status["running"] = True
    _pipeline_status["last_run"] = datetime.now().isoformat()
    try:
        result = subprocess.run(
            [sys.executable, "pipeline_runner.py"],
            capture_output=True, text=True, timeout=900,
            cwd=str(Path(__file__).resolve().parent),
        )
        _pipeline_status["last_result"] = {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:] if result.stdout else "",
            "stderr_tail": result.stderr[-1000:] if result.stderr else "",
            "finished": datetime.now().isoformat(),
            "trigger": "scheduled",
        }
    except Exception as e:
        _pipeline_status["last_result"] = {
            "exit_code": -1,
            "error": str(e),
            "finished": datetime.now().isoformat(),
            "trigger": "scheduled",
        }
    finally:
        _pipeline_status["running"] = False


# Only start scheduler in production (gunicorn), not in dev reload
if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PUBLIC_DOMAIN"):
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            _scheduled_pipeline_run,
            "cron",
            hour=11, minute=0,  # 11:00 UTC = 6:00 AM CT
            id="daily_pipeline",
            replace_existing=True,
        )
        scheduler.start()
        print("APScheduler started: daily pipeline at 11:00 UTC (6 AM CT)")
    except ImportError:
        print("WARNING: APScheduler not installed, daily runs disabled")


# -- Main ---------------------------------------------------------------------
if __name__ == "__main__":
    print("Multi-Site Blog Agent Dashboard")
    print("http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
