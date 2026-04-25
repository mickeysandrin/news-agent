"""Daily news agent entrypoint.

Flow:
  fetch → dedup (skip seen) → curate (LLM pass 1) → write (LLM pass 2)
        → send email → record sent + seen
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from . import curate, dedup, email_send, fetch, feedback

log = logging.getLogger("news-agent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write preview HTML locally instead of sending. Still hits the LLM.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Stop after fetch+dedup. Useful for debugging source feeds.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    # load config
    sources_path = Path(__file__).parent / "sources.yaml"
    with open(sources_path, encoding="utf-8") as f:
        sources_config = yaml.safe_load(f)

    # 1. fetch
    log.info("fetching sources…")
    stories = fetch.fetch_all(sources_config)
    log.info("fetched %d stories total", len(stories))

    # 2. dedup (only if we have Turso creds; useful for local-first debugging)
    if os.environ.get("TURSO_DATABASE_URL") and not args.skip_llm:
        stories = dedup.filter_seen(stories)
    else:
        log.warning("skipping dedup (no TURSO_DATABASE_URL set)")

    if not stories:
        log.info("nothing fresh today — skipping email")
        return 0

    if args.skip_llm:
        for s in stories[:20]:
            print(f"[{s.topic}] {s.source}: {s.title}")
        return 0

    # 3. build preference profile from recent votes
    profile = ""
    if os.environ.get("TURSO_DATABASE_URL"):
        profile = feedback.build_preference_profile()
        if profile:
            log.info("loaded preference profile (%d chars)", len(profile))

    # 4. curate
    log.info("curating…")
    picked = curate.curate(stories, profile)
    if not picked:
        log.warning("curator returned no picks")
        return 1

    # 5. write
    log.info("writing digest…")
    feedback_url = os.environ.get("FEEDBACK_BASE_URL", "")
    if not feedback_url:
        log.warning("FEEDBACK_BASE_URL not set — email will have broken vote links")
    markdown, sent_stories = curate.write_digest(picked, profile, feedback_url)

    # 6. send
    email_send.send_digest(markdown, dry_run=args.dry_run)

    # 7. persist post-send only: if Resend raises above, tomorrow's run gets a
    # clean retry instead of finding everything already marked seen.
    if os.environ.get("TURSO_DATABASE_URL") and not args.dry_run:
        dedup.record_sent(sent_stories)
        dedup.record_seen(sent_stories)
    return 0


if __name__ == "__main__":
    sys.exit(main())
