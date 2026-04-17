from __future__ import annotations
import json
import os

import anthropic

import config
from modules.site_crawler import get_austin_pillar_links, get_related_posts

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "write_blog.txt")


def load_prompt() -> str:
    with open(PROMPT_PATH, "r") as f:
        return f.read()


def build_link_context(all_links: list[dict], topic_keywords: list[str]) -> str:
    """Build a string of available internal links for the prompt."""
    pillar_links = get_austin_pillar_links(all_links)
    related_posts = get_related_posts(all_links, topic_keywords)

    lines = ["## Austin Pillar Pages (MUST link to at least 2 of these):"]
    for l in pillar_links:
        lines.append(f"- [{l['title']}]({l['url']})")

    lines.append("\n## Related Blog Posts (link to 2-3 of these):")
    for l in related_posts:
        lines.append(f"- [{l['title']}]({l['url']})")

    if not related_posts:
        lines.append("- (No closely related posts found — focus on pillar page links)")

    return "\n".join(lines)


def generate_blog_post(question: str, topic: str, keywords: str,
                       context: str, all_links: list[dict]) -> dict | None:
    """Use Claude to write a full SEO blog post answering the client's question.

    Returns {title, slug, content_html, categories, tags, meta_description}.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    system_prompt = load_prompt()

    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    link_context = build_link_context(all_links, kw_list)

    user_message = f"""## Client Question
{question}

## Topic
{topic}

## Keywords
{keywords}

## Context from the Call
{context}

## Available Internal Links
{link_context}

## Site URL
{config.WP_SITE_URL}

Write the blog post now. Return your response as a JSON object."""

    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip()

    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        post = json.loads(text)
    except json.JSONDecodeError:
        print(f"Failed to parse blog post JSON: {text[:200]}")
        return None

    required = ["title", "slug", "content_html", "categories", "tags", "meta_description"]
    for key in required:
        if key not in post:
            print(f"Missing key '{key}' in blog post response.")
            return None

    return post
