"""URL-hash dedup against Turso. Skips stories we've already emailed inside the dedup window."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import libsql_client

from .fetch import Story

log = logging.getLogger(__name__)

# Stories sent within this many days are filtered out of future digests.
# Older rows are kept (see PRUNE_AFTER_DAYS) for debugging but no longer
# block re-surfacing — useful if a topic dries up and an evergreen piece
# is worth a second look.
DEDUP_WINDOW_DAYS = 7

# How long to keep rows in seen_urls before pruning. Larger than the dedup
# window so we have a small audit trail when investigating "why did this
# resurface" without bloating the table.
PRUNE_AFTER_DAYS = 30


def _client():
    # Force the HTTP transport (https://) instead of WebSocket (libsql://):
    # the WS handshake hits a 505 from Turso's public endpoint on GitHub
    # Actions runners. Same swap the Cloudflare Worker does.
    url = os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://", 1)
    return libsql_client.create_client_sync(
        url=url,
        auth_token=os.environ["TURSO_AUTH_TOKEN"],
    )


def filter_seen(stories: list[Story]) -> list[Story]:
    """Return only stories we haven't sent inside the dedup window.

    Read-only — recording happens in record_seen() after a successful send,
    so a failed run doesn't burn stories and leave tomorrow with nothing
    fresh."""
    if not stories:
        return []

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)
    ).isoformat()

    with _client() as db:
        ids = [s.id for s in stories]
        placeholders = ",".join(["?"] * len(ids))
        rs = db.execute(
            f"SELECT url_hash FROM seen_urls "
            f"WHERE url_hash IN ({placeholders}) AND first_seen >= ?",
            [*ids, cutoff],
        )
        already_seen = {row[0] for row in rs.rows}

    fresh = [s for s in stories if s.id not in already_seen]
    log.info("dedup: %d total → %d fresh (skipped %d seen in last %dd)",
             len(stories), len(fresh), len(already_seen), DEDUP_WINDOW_DAYS)
    return fresh


def prune_seen() -> None:
    """Drop rows older than PRUNE_AFTER_DAYS. Cheap thanks to the
    idx_seen_urls_first_seen index. Safe to call every run."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=PRUNE_AFTER_DAYS)
    ).isoformat()
    with _client() as db:
        rs = db.execute(
            "DELETE FROM seen_urls WHERE first_seen < ?",
            [cutoff],
        )
    # rs.rows_affected isn't always populated by libsql; log best-effort
    log.info("prune: cleared seen_urls rows older than %dd", PRUNE_AFTER_DAYS)


def record_seen(stories: list[Story]) -> None:
    """Mark stories as seen so future runs skip them. Call only after the
    email actually went out — see filter_seen()."""
    if not stories:
        return

    now = datetime.now(timezone.utc).isoformat()
    with _client() as db:
        db.batch([
            libsql_client.Statement(
                "INSERT OR IGNORE INTO seen_urls (url_hash, first_seen) VALUES (?, ?)",
                [s.id, now],
            )
            for s in stories
        ])


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
