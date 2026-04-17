import json
import os

import anthropic

import config
from modules import db

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "extract_questions.txt")


def load_prompt() -> str:
    with open(PROMPT_PATH, "r") as f:
        return f.read()


def extract_questions(transcript: str, call_id: str) -> list[dict]:
    """Use Claude to extract sober-living-related questions from a call transcript.

    Returns list of {question, topic, keywords, context}.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    system_prompt = load_prompt()

    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2000,
        system=system_prompt,
        messages=[
            {"role": "user", "content": f"Here is the call transcript:\n\n{transcript}"}
        ],
    )

    text = response.content[0].text.strip()

    # Parse JSON response
    try:
        # Handle markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        questions = json.loads(text)
    except json.JSONDecodeError:
        print(f"Failed to parse Claude response for call {call_id}: {text[:200]}")
        return []

    if not isinstance(questions, list):
        questions = [questions]

    # Filter duplicates
    unique = []
    for q in questions:
        question_text = q.get("question", "")
        if not question_text:
            continue
        if db.is_question_duplicate(question_text):
            print(f"  Skipping duplicate question: {question_text[:60]}...")
            continue
        unique.append(q)

    return unique


def analyze_transcript(call_id: str, transcript: str, site_id: str = "eudaimonia") -> list[dict]:
    """Full pipeline: extract questions, save to DB, return them."""
    if db.is_call_processed(call_id):
        print(f"  Call {call_id} already processed, skipping.")
        return []

    questions = extract_questions(transcript, call_id)

    saved = []
    for q in questions:
        keywords = q.get("keywords", "")
        if isinstance(keywords, list):
            keywords = ", ".join(keywords)

        qid = db.save_question(
            call_id=call_id,
            question=q["question"],
            topic=q.get("topic", ""),
            keywords=keywords,
            context=q.get("context", ""),
            site_id=site_id,
        )
        q["id"] = qid
        saved.append(q)

    db.mark_call_processed(call_id, len(transcript), site_id=site_id)
    print(f"  Extracted {len(saved)} questions from call {call_id} for site '{site_id}'.")
    return saved
