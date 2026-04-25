"""Fetchers for each source type defined in sources.yaml."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx
from dateutil import parser as date_parser

log = logging.getLogger(__name__)

# Only consider stories published in the last N hours.
# Football and cycling race results age fast; long-form essays less so,
# but the LLM curator handles recency as a soft preference — this is
# just a hard cutoff.
LOOKBACK_HOURS = 36


@dataclass
class Story:
    id: str                # stable hash of URL
    title: str
    url: str
    source: str
    topic: str
    summary: str           # short text from the feed
    published: str         # ISO 8601
    weight: float          # source weight, for LLM context

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _story_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _within_lookback(published_str: str | None) -> bool:
    if not published_str:
        # if the feed didn't give us a date, include it and let the LLM decide
        return True
    try:
        dt = date_parser.parse(published_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def fetch_rss(source: dict, topic_key: str) -> list[Story]:
    """Fetch items from an RSS/Atom feed."""
    try:
        # feedparser handles both RSS and Atom transparently
        feed = feedparser.parse(source["url"])
    except Exception as e:
        log.warning("feed fetch failed for %s: %s", source["name"], e)
        return []

    stories: list[Story] = []
    for entry in feed.entries[:20]:  # per-feed cap, pre-curation
        url = entry.get("link", "")
        if not url:
            continue

        published = (
            entry.get("published")
            or entry.get("updated")
            or entry.get("pubDate")
            or ""
        )
        if not _within_lookback(published):
            continue

        # feed summaries can contain raw HTML; strip aggressively
        raw_summary = entry.get("summary") or entry.get("description") or ""
        summary = _strip_html(raw_summary)[:500]

        stories.append(Story(
            id=_story_id(url),
            title=entry.get("title", "(untitled)").strip(),
            url=url,
            source=source["name"],
            topic=topic_key,
            summary=summary,
            published=published,
            weight=float(source.get("weight", 1.0)),
        ))
    return stories


def fetch_hn_algolia(source: dict, topic_key: str) -> list[Story]:
    """Hacker News front page via Algolia."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(source["url"])
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("HN fetch failed: %s", e)
        return []

    stories: list[Story] = []
    for hit in data.get("hits", [])[:25]:
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        title = hit.get("title") or hit.get("story_title") or ""
        if not title:
            continue

        created_at = hit.get("created_at", "")
        if not _within_lookback(created_at):
            continue

        # HN has comment counts and points — stash those in summary so the
        # curator has social signal to weigh
        points = hit.get("points", 0)
        comments = hit.get("num_comments", 0)
        summary = f"[HN: {points} points, {comments} comments]"

        stories.append(Story(
            id=_story_id(url),
            title=title,
            url=url,
            source=source["name"],
            topic=topic_key,
            summary=summary,
            published=created_at,
            weight=float(source.get("weight", 1.0)),
        ))
    return stories


def _strip_html(text: str) -> str:
    """Very basic HTML→text. Good enough for feed summaries."""
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


FETCHERS = {
    "rss": fetch_rss,
    "hn_algolia": fetch_hn_algolia,
}


def fetch_all(sources_config: dict) -> list[Story]:
    """Fetch every source in the config; return a flat list of stories."""
    all_stories: list[Story] = []
    for topic_key, topic in sources_config["topics"].items():
        for source in topic["sources"]:
            fetcher = FETCHERS.get(source["type"])
            if not fetcher:
                log.warning("unknown source type: %s", source["type"])
                continue
            stories = fetcher(source, topic_key)
            log.info("%-30s  %3d stories", source["name"], len(stories))
            all_stories.extend(stories)
    return all_stories
