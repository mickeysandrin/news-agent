"""Builds a preference profile from recent votes to inject into the curation prompt.

Design choice: prompt-level preference learning (not embeddings).
Reasons: (1) vote volume will be low (~10–30/day), (2) the profile is
inspectable and human-editable, (3) zero vector-DB cost. Works well up to
~a few hundred votes; if you ever exceed that and want more nuance,
this is the layer to swap out.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import libsql_client

log = logging.getLogger(__name__)

# Look at votes from the last N days when building the profile.
PROFILE_WINDOW_DAYS = 30


def build_preference_profile() -> str:
    """Return a short natural-language profile describing what Mickey likes/dislikes.

    Returns empty string if there aren't enough votes yet — the prompt handles
    that case gracefully.
    """
    try:
        # See dedup._client() for why we force the https transport.
        url = os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://", 1)
        with libsql_client.create_client_sync(
            url=url,
            auth_token=os.environ["TURSO_AUTH_TOKEN"],
        ) as db:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=PROFILE_WINDOW_DAYS)).isoformat()
            rs = db.execute(
                """SELECT story_title, story_source, story_topic, vote
                   FROM votes WHERE voted_at >= ?""",
                [cutoff],
            )
            rows = list(rs.rows)
    except Exception as e:
        log.warning("couldn't load votes: %s", e)
        return ""

    if len(rows) < 5:
        # not enough signal to be useful yet
        return ""

    up_sources: Counter[str] = Counter()
    down_sources: Counter[str] = Counter()
    up_topics: Counter[str] = Counter()
    down_topics: Counter[str] = Counter()
    up_titles: list[str] = []
    down_titles: list[str] = []

    for title, source, topic, vote in rows:
        if vote == 1:
            up_sources[source] += 1
            up_topics[topic] += 1
            up_titles.append(title)
        elif vote == -1:
            down_sources[source] += 1
            down_topics[topic] += 1
            down_titles.append(title)

    parts: list[str] = ["## Reader preferences (from recent feedback)\n"]

    if up_sources:
        top = [s for s, _ in up_sources.most_common(5)]
        parts.append(f"Frequently upvoted sources: {', '.join(top)}")
    if down_sources:
        bot = [s for s, _ in down_sources.most_common(3)]
        parts.append(f"Frequently downvoted sources: {', '.join(bot)}")
    if up_titles:
        parts.append("Recently upvoted story examples:")
        parts.extend(f"  + {t}" for t in up_titles[-8:])
    if down_titles:
        parts.append("Recently downvoted story examples:")
        parts.extend(f"  - {t}" for t in down_titles[-8:])

    parts.append(
        "\nUse these signals to bias curation. Prefer stories that resemble the "
        "upvoted examples in tone, angle, or subject. Avoid anything that resembles "
        "the downvoted ones. These are preferences, not hard rules."
    )
    return "\n".join(parts)
