"""
Eudaimonia Homes — Blog Agent
Pulls RingCentral call transcripts, extracts client questions about sober living,
generates SEO blog posts with internal linking, and schedules them on WordPress.
"""
from __future__ import annotations

import argparse
import sys

import config
from modules import db
from modules.ringcentral_client import RingCentralClient
from modules.transcript_analyzer import analyze_transcript
from modules.site_crawler import crawl_site
from modules.blog_writer import generate_blog_post
from modules.wordpress_client import WordPressClient
from modules.scheduler import schedule_post


def run_pipeline(max_posts: int | None = None):
    """Run the full pipeline: transcripts → questions → blogs → schedule."""

    max_posts = max_posts or config.POSTS_PER_DAY
    print("=" * 60)
    print("Eudaimonia Blog Agent — Starting Pipeline")
    print("=" * 60)

    # 1. Init DB
    db.init_db()

    # 2. Connect to WordPress
    print("\n[1/5] Connecting to WordPress...")
    wp = WordPressClient()
    if not wp.test_connection():
        print("ABORT: Cannot connect to WordPress. Check credentials in .env")
        sys.exit(1)

    # 3. Crawl site for internal links
    print("\n[2/5] Refreshing site structure...")
    all_links = crawl_site(wp)
    print(f"  {len(all_links)} internal links available.")

    # 4. Fetch transcripts from RingCentral
    print("\n[3/5] Fetching RingCentral transcripts...")
    rc = RingCentralClient()
    rc.login()
    calls = rc.get_calls_with_transcripts()
    print(f"  {len(calls)} new calls with transcripts.")

    # 5. Analyze transcripts → extract questions
    print("\n[4/5] Analyzing transcripts...")
    new_questions = []
    for call in calls:
        questions = analyze_transcript(call["call_id"], call["transcript"])
        new_questions.extend(questions)
    print(f"  {len(new_questions)} new questions extracted.")

    # Also grab any pending questions from previous runs
    pending = db.get_pending_questions(limit=max_posts)
    print(f"  {len(pending)} total pending questions in queue.")

    if not pending:
        print("\nNo pending questions to process. Done.")
        return

    # 6. Generate and schedule blog posts
    print(f"\n[5/5] Generating and scheduling up to {max_posts} blog posts...")
    posts_created = 0

    for q in pending:
        if posts_created >= max_posts:
            break

        print(f"\n  Processing: {q['question'][:60]}...")
        keywords = q.get("keywords", "")

        post_data = generate_blog_post(
            question=q["question"],
            topic=q.get("topic", ""),
            keywords=keywords,
            context=q.get("context", ""),
            all_links=all_links,
        )

        if not post_data:
            print(f"  SKIP: Failed to generate post for question #{q['id']}")
            continue

        try:
            wp_resp = schedule_post(wp, post_data, q["id"])
            posts_created += 1
            print(f"  OK: '{post_data['title']}' → WP #{wp_resp.get('id')}")
        except Exception as e:
            print(f"  ERROR scheduling post: {e}")

    print(f"\n{'=' * 60}")
    print(f"Pipeline complete. {posts_created} posts scheduled.")
    print(f"{'=' * 60}")


def test_connections():
    """Test all API connections without running the full pipeline."""
    db.init_db()

    print("Testing WordPress connection...")
    wp = WordPressClient()
    wp_ok = wp.test_connection()

    print("\nTesting RingCentral connection...")
    rc = RingCentralClient()
    rc_ok = rc.test_connection()

    print("\nTesting Anthropic API...")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": "Say 'connected' in one word."}],
        )
        print(f"  Anthropic API: {resp.content[0].text.strip()}")
        anthropic_ok = True
    except Exception as e:
        print(f"  Anthropic API failed: {e}")
        anthropic_ok = False

    print(f"\n--- Results ---")
    print(f"  WordPress:   {'OK' if wp_ok else 'FAIL'}")
    print(f"  RingCentral: {'OK' if rc_ok else 'FAIL'}")
    print(f"  Anthropic:   {'OK' if anthropic_ok else 'FAIL'}")


def crawl_only():
    """Only crawl the site and display the link map (for verification)."""
    db.init_db()
    wp = WordPressClient()
    links = crawl_site(wp, force=True)

    print(f"\n--- Site Link Map ({len(links)} links) ---\n")
    for link in links:
        print(f"  [{link['link_type']:15s}] {link['title'][:50]:50s} → {link['url']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eudaimonia Blog Agent")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "test", "crawl"],
                        help="run=full pipeline, test=test connections, crawl=crawl site only")
    parser.add_argument("--max-posts", type=int, default=None,
                        help=f"Max posts to generate (default: {config.POSTS_PER_DAY})")

    args = parser.parse_args()

    if args.command == "test":
        test_connections()
    elif args.command == "crawl":
        crawl_only()
    else:
        run_pipeline(max_posts=args.max_posts)
