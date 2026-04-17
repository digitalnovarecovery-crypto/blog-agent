from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
from modules import db


def get_next_publish_slot() -> str:
    """Find the next available publish slot (2x/day at configured times).

    Returns ISO 8601 datetime string in the site's timezone.
    """
    tz = ZoneInfo(config.TIMEZONE)
    now = datetime.now(tz)

    # Get already-scheduled times
    scheduled = set(db.get_scheduled_times())

    # Look up to 30 days ahead to find a free slot
    for day_offset in range(30):
        day = now.date() + timedelta(days=day_offset)

        for time_str in config.PUBLISH_TIMES:
            hour, minute = map(int, time_str.split(":"))
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)

            # Skip past slots
            if slot <= now:
                continue

            slot_iso = slot.isoformat()
            if slot_iso not in scheduled:
                return slot_iso

    raise RuntimeError("No available publish slots in the next 30 days.")


def schedule_post(wp_client, post_data: dict, question_id: int) -> dict:
    """Schedule a blog post for the next available time slot.

    Args:
        wp_client: WordPressClient instance
        post_data: {title, slug, content_html, categories, tags, meta_description}
        question_id: ID of the extracted question this post answers

    Returns:
        WordPress API response for the created post.
    """
    slot = get_next_publish_slot()
    print(f"  Scheduling post for: {slot}")

    # Resolve category and tag names to IDs
    cat_ids = wp_client.resolve_categories(post_data["categories"])
    tag_ids = wp_client.resolve_tags(post_data["tags"])

    wp_response = wp_client.create_post(
        title=post_data["title"],
        content=post_data["content_html"],
        slug=post_data["slug"],
        category_ids=cat_ids,
        tag_ids=tag_ids,
        status="future",
        date=slot,
        meta_description=post_data.get("meta_description", ""),
    )

    wp_post_id = wp_response.get("id", 0)

    db.save_published_post(
        question_id=question_id,
        wp_post_id=wp_post_id,
        title=post_data["title"],
        slug=post_data["slug"],
        scheduled_time=slot,
    )

    db.mark_question_used(question_id)

    print(f"  Post '{post_data['title']}' scheduled as WP post #{wp_post_id}")
    return wp_response
