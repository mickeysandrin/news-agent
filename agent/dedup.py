"""URL-hash dedup against Turso. Skips stories we've already emailed in the last 14 days."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import libsql_client

from .fetch import Story

log = logging.getLogger(__name__)


def _client():
    return libsql_client.create_client_sync(
        url=os.environ["TURSO_DATABASE_URL"],
        auth_token=os.environ["TURSO_AUTH_TOKEN"],
    )


def filter_seen(stories: list[Story]) -> list[Story]:
    """Return only stories we haven't seen before. Records the new ones as seen."""
    if not stories:
        return []

    now = datetime.now(timezone.utc).isoformat()

    with _client() as db:
        # batch the 'seen?' check
        ids = [s.id for s in stories]
        placeholders = ",".join(["?"] * len(ids))
        rs = db.execute(
            f"SELECT url_hash FROM seen_urls WHERE url_hash IN ({placeholders})",
            ids,
        )
        already_seen = {row[0] for row in rs.rows}

        fresh = [s for s in stories if s.id not in already_seen]

        # record the fresh ones so tomorrow we skip them
        if fresh:
            db.batch([
                libsql_client.Statement(
                    "INSERT OR IGNORE INTO seen_urls (url_hash, first_seen) VALUES (?, ?)",
                    [s.id, now],
                )
                for s in fresh
            ])

        log.info("dedup: %d total → %d fresh (skipped %d seen)",
                 len(stories), len(fresh), len(already_seen))

    return fresh


def record_sent(stories: list[Story]) -> None:
    """Persist what actually got sent, so feedback links can look them up."""
    if not stories:
        return

    now = datetime.now(timezone.utc).isoformat()
    with _client() as db:
        db.batch([
            libsql_client.Statement(
                """INSERT OR REPLACE INTO sent_stories
                   (story_id, title, source, topic, url, sent_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [s.id, s.title, s.source, s.topic, s.url, now],
            )
            for s in stories
        ])
