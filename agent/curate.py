"""LLM curation + writing. Two passes:
   1. Pick the best ~3-5 stories per topic (structured JSON response).
   2. Write the full email in a Morning-Brew-adjacent voice (Markdown → HTML later).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import anthropic

from .fetch import Story

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"  # swap to "claude-sonnet-4-6" for higher-quality writing (~5x cost)

# How many stories to surface per topic in the final email.
# Some topics (cycling in winter, say) may naturally return fewer — the
# curator is explicitly told that's fine.
MAX_STORIES_PER_TOPIC = 5

CURATOR_SYSTEM = """You are a personal news curator for Mickey, a London-based Senior Product Manager at Zen Educate. He's Italian, engaged, lives in Vauxhall. He cares about:

- Tech/AI and Product Management (practitioner level — he leads a PM team and is deep in AI tooling)
- EdTech and the UK education industry (this is his day job)
- Arsenal FC, Champions League, World Cup cycles
- Cycling (he rides, has been to Mallorca, plans to go to Girona)
- Punchy business news in the Morning Brew vein — markets, deals, fun business stories

Your job: given a set of candidate stories tagged by topic, pick the strongest N per topic for a morning digest. "Strongest" means:
- High signal: genuinely new information, not a rehash
- Interesting angle: a story that changes how he thinks, not just "X happened"
- Practitioner-relevant for work topics: PM craft, AI product patterns, UK schools policy
- Timely: Arsenal/CL results from last night trump older analysis
- Avoid duplicates: if two sources cover the same story, keep the better one

Skip:
- Pure churn (minor feature announcements, daily market moves without a story)
- Clickbait and listicles
- Anything you've clearly seen before

Return STRICTLY valid JSON in this shape:
{
  "picks": [
    {
      "story_id": "...",
      "topic": "...",
      "angle": "one sentence describing why this story is worth reading today"
    }
  ]
}
No prose outside the JSON."""


WRITER_SYSTEM = """You write Mickey's daily news digest. Tone: Morning Brew — punchy, smart, a bit of dry wit, always respectful of the reader's time. Never cheesy, never lecturing. British English spelling.

The email template already renders a "Daily Digest" masthead and the date above your output. Do NOT emit a title, header, or date line yourself — start directly with the intro paragraph. Do NOT use horizontal-rule separators (---), em-dashes (—), or any standalone dash characters on their own line as soft separators between sections; the section headings provide enough separation.

Structure:
- Start with a 1-2 sentence intro that references the date or something topical. Keep it light.
- Then six sections in this order, each with a short (5-8 word) title:
  1. Tech, AI & Product
  2. EdTech & Education
  3. Arsenal & Football
  4. Cycling
  5. Business & Markets
  6. One thing worth your attention today (single standout pick from the day, doesn't have to be a topic match — the best story of the bunch). This pick MUST be a story you have not already used in any other section; do not recycle a story from above.

For each story:
- A short punchy headline (your own words, not the source's).
- A small italic byline directly under the headline, with the source name as a clickable markdown link to the article URL: `*via [Source Name](article_url)*`. The source name itself must be the link text — never plain text.
- 2-4 sentences of context. Tell him what happened AND why it matters. Don't just summarise; add a view or a useful framing.
- A "Read it →" link to the same article URL at the end.

Rules:
- If a topic has no strong stories, write one line acknowledging that ("Quiet day in cycling — nothing worth your time.") and move on. Better short than padded.
- If Arsenal played last night, LEAD the Arsenal section with the result. If they lost, don't be sycophantic — be honest.
- Never reproduce long quotes from source articles. Paraphrase. One short quote maximum per digest if it's genuinely valuable.
- If the Morning Brew source is in the pool, extract the topic and re-report in your own words. Do not copy their sentences.
- End with a one-line sign-off. Keep it varied across days.

Output pure Markdown. The template wrapper handles HTML conversion."""


def curate(stories: list[Story], preference_profile: str) -> list[Story]:
    """First pass: LLM picks the best stories across topics."""
    if not stories:
        return []

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # keep the payload compact — we don't need to send full summaries for ranking
    candidates = [
        {
            "story_id": s.id,
            "topic": s.topic,
            "title": s.title,
            "source": s.source,
            "source_weight": s.weight,
            "summary": s.summary[:200],
            "published": s.published,
        }
        for s in stories
    ]

    user_msg = (
        f"{preference_profile}\n\n"
        if preference_profile else ""
    ) + (
        f"Today is {datetime.now().strftime('%A %-d %B %Y')}.\n\n"
        f"Pick up to {MAX_STORIES_PER_TOPIC} stories per topic. Return JSON only.\n\n"
        f"Candidates ({len(candidates)} total):\n{json.dumps(candidates, indent=2)}"
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=CURATOR_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = resp.content[0].text.strip()
    # Haiku sometimes wraps JSON in ```json fences despite instructions
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error("curator returned invalid JSON: %s\n---\n%s", e, raw[:500])
        return []

    picked_ids = {p["story_id"] for p in payload.get("picks", [])}
    # keep original Story objects (we need URLs + weights for the writer pass)
    by_id = {s.id: s for s in stories}
    picked = [by_id[i] for i in picked_ids if i in by_id]

    log.info("curator picked %d stories from %d candidates", len(picked), len(stories))
    return picked


def write_digest(
    picked: list[Story],
    preference_profile: str,
    feedback_base_url: str,
) -> tuple[str, list[Story]]:
    """Second pass: LLM writes the digest in Markdown. Returns (markdown, picked_stories_in_order)."""
    if not picked:
        return "_No stories today — all sources were quiet._", []

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Group by topic for the writer's context
    by_topic: dict[str, list[Story]] = {}
    for s in picked:
        by_topic.setdefault(s.topic, []).append(s)

    # Give the writer full summaries for the stories it chose
    payload: dict[str, Any] = {}
    for topic, items in by_topic.items():
        payload[topic] = [
            {
                "id": s.id,
                "title": s.title,
                "source": s.source,
                "summary": s.summary,
                "url": s.url,
                "published": s.published,
            }
            for s in items
        ]

    user_msg = (
        f"{preference_profile}\n\n" if preference_profile else ""
    ) + (
        f"Today: {datetime.now().strftime('%A %-d %B %Y')}.\n\n"
        f"Write the digest. Use these stories, grouped by topic:\n\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        f"For each story, you MUST emit TWO separate markdown links to the same article URL — do not skip either:\n"
        f"  1. An italic source byline directly under the headline, formatted EXACTLY: `*via [SOURCE_NAME](ARTICLE_URL)*` "
        f"(use the story's `source` and `url` fields).\n"
        f"  2. After the 2-4 sentence context paragraph, a 'Read it →' call-to-action link formatted EXACTLY: "
        f"`[Read it →](ARTICLE_URL)`. This MUST be a markdown link, not plain text, even though it targets the same URL as the byline.\n"
        f"Under each 'Read it →' link, on the next line, include two feedback links formatted EXACTLY like this:\n"
        f"`<sub>👍 [More like this]({feedback_base_url}/vote?id=STORY_ID&vote=up) · "
        f"👎 [Less like this]({feedback_base_url}/vote?id=STORY_ID&vote=down)</sub>`\n"
        f"Replace STORY_ID with the story's `id` field exactly."
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=WRITER_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    return resp.content[0].text, picked
