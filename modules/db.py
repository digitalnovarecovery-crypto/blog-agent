from __future__ import annotations

import sqlite3
import os
from datetime import datetime
from typing import Optional

import config


def get_connection():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS processed_calls (
            call_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL,
            transcript_length INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS extracted_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id TEXT NOT NULL,
            question TEXT NOT NULL,
            topic TEXT,
            keywords TEXT,
            context TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY (call_id) REFERENCES processed_calls(call_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS published_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER,
            wp_post_id INTEGER,
            title TEXT NOT NULL,
            slug TEXT,
            scheduled_time TEXT,
            status TEXT DEFAULT 'scheduled',
            created_at TEXT NOT NULL,
            FOREIGN KEY (question_id) REFERENCES extracted_questions(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS internal_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            link_type TEXT,
            keywords TEXT,
            last_updated TEXT
        )
    """)

    conn.commit()
    conn.close()


def is_call_processed(call_id: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM processed_calls WHERE call_id = ?", (call_id,)
    ).fetchone()
    conn.close()
    return row is not None


def mark_call_processed(call_id: str, transcript_length: int, site_id: str = ""):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO processed_calls (call_id, processed_at, transcript_length, site_id) VALUES (?, ?, ?, ?)",
        (call_id, datetime.now().isoformat(), transcript_length, site_id),
    )
    conn.commit()
    conn.close()


def save_question(call_id: str, question: str, topic: str, keywords: str, context: str,
                   site_id: str = "eudaimonia") -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO extracted_questions (call_id, question, topic, keywords, context, site_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (call_id, question, topic, keywords, context, site_id, datetime.now().isoformat()),
    )
    conn.commit()
    qid = c.lastrowid
    conn.close()
    return qid


def get_pending_questions(limit: int = 10, site_id: str | None = None) -> list[dict]:
    conn = get_connection()
    if site_id:
        rows = conn.execute(
            "SELECT * FROM extracted_questions WHERE status = 'pending' AND site_id = ? ORDER BY created_at ASC LIMIT ?",
            (site_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM extracted_questions WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_question_used(question_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE extracted_questions SET status = 'used' WHERE id = ?", (question_id,)
    )
    conn.commit()
    conn.close()


def save_published_post(question_id: int, wp_post_id: int, title: str, slug: str, scheduled_time: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO published_posts (question_id, wp_post_id, title, slug, scheduled_time, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (question_id, wp_post_id, title, slug, scheduled_time, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_scheduled_times() -> list[str]:
    """Return all scheduled_time values for posts with status='scheduled'."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT scheduled_time FROM published_posts WHERE status = 'scheduled'"
    ).fetchall()
    conn.close()
    return [r["scheduled_time"] for r in rows]


def is_question_duplicate(question: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM extracted_questions WHERE question = ?", (question,)
    ).fetchone()
    conn.close()
    return row is not None


# --- Internal links cache ---

def save_internal_links(links: list[dict]):
    conn = get_connection()
    now = datetime.now().isoformat()
    for link in links:
        conn.execute(
            """INSERT OR REPLACE INTO internal_links (url, title, link_type, keywords, last_updated)
               VALUES (?, ?, ?, ?, ?)""",
            (link["url"], link["title"], link.get("link_type", "blog"), link.get("keywords", ""), now),
        )
    conn.commit()
    conn.close()


def get_internal_links() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM internal_links").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_links_last_updated() -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(last_updated) as lu FROM internal_links"
    ).fetchone()
    conn.close()
    return row["lu"] if row else None
