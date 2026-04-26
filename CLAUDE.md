# CLAUDE.md

Primer for Claude Code working in this repo. Read this first.

## What this is

A personal daily news digest agent. Runs at ~06:00 London time via GitHub
Actions, fetches RSS + HN feeds across five topics, uses Claude (Haiku 4.5)
to curate and write the email in a Morning-Brew-style voice, sends via
Resend, and learns from 👍/👎 votes captured by a Cloudflare Worker writing
to Turso.

The reader is **Mickey** — Italian, lives in London, Senior PM at Zen Educate
(EdTech). Engaged, Arsenal fan, cyclist, into AI/PM craft. The voice and
topic mix are tailored to him; don't generalise this into a generic news
agent.

## Architecture in one diagram

```
GitHub Actions (cron, daily)
        │
        ▼
agent/main.py
   1. fetch.py        → RSS + HN Algolia → list[Story]
   2. dedup.py        → skip URLs sent in last 7 days (Turso, windowed)
   3. feedback.py     → build preference profile from recent votes
   4. curate.py       → LLM pass 1: pick best ~5/topic (JSON out)
   5. curate.py       → LLM pass 2: write digest in Markdown
   6. email_send.py   → Markdown → HTML → Resend
   7. dedup.record_sent + record_seen + prune_seen →
                          only after a successful send, so a failed run
                          doesn't burn URLs. prune_seen drops rows older
                          than 30 days each run.

Cloudflare Worker (worker/feedback-worker.js)
   GET /vote?id=…&vote=up|down  →  insert into votes table (Turso)
```

## Key files (in priority order if you only read a few)

- `agent/curate.py` — **the soul of the project**. Two prompts (`CURATOR_SYSTEM`,
  `WRITER_SYSTEM`) plus the JSON-parsing glue. Most quality improvements
  happen here. Don't refactor without a concrete reason.
- `agent/sources.yaml` — the input layer. Mickey edits this himself; treat
  source choice as his decision, not yours.
- `agent/main.py` — orchestrator. Should stay short and linear.
- `agent/fetch.py` — fetchers per source type. Add new types here if needed.
- `email_templates/digest.html` — Jinja template with inline CSS for Gmail.
- `worker/feedback-worker.js` — Cloudflare Worker; talks to Turso over HTTP.

## Conventions

- **Python 3.12, no framework.** Plain stdlib + a few well-chosen libs
  (`anthropic`, `feedparser`, `httpx`, `jinja2`, `libsql-client`, `pyyaml`,
  `resend`, `python-dateutil`). Don't add FastAPI, Pydantic, Celery, etc.
  This is a single-file-per-concern script, not an app.
- **British English** in user-facing copy and prompts.
- **Logging over print.** Use `log = logging.getLogger(__name__)`.
- **Comment the *why*, not the *what*.** Existing comments follow this —
  they explain design choices (e.g. "prompt-level preference learning beats
  embeddings here because vote volume is low and the profile should be
  human-editable"). Match that style; skip narration.
- **Type hints** on function signatures. `from __future__ import annotations`
  at the top of every module so we can use modern syntax on 3.12.
- **No `try/except: pass`.** Either log the exception or let it propagate.
  The fetch layer logs and returns `[]` on failure (one bad feed shouldn't
  kill the run); everything else should fail loudly.
- **Models**: `claude-haiku-4-5` for both LLM passes by default. Switching
  to `claude-sonnet-4-6` is a single-line change in `curate.py` and brings
  cost from ~£2/mo to ~£8/mo. Mickey's budget ceiling is £15/mo.

## Running locally

```bash
pip install -r requirements.txt
# .env file (gitignored) holds:
#   ANTHROPIC_API_KEY, RESEND_API_KEY, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN,
#   EMAIL_TO, EMAIL_FROM, FEEDBACK_BASE_URL
set -a; source .env; set +a

python -m agent.main --skip-llm   # fetch + dedup only, prints results
python -m agent.main --dry-run    # full LLM pipeline, writes digest_preview.html instead of sending
python -m agent.main               # actually sends
```

## Things that will break and how to debug

- **An RSS feed returns 0 stories.** Run `--skip-llm`, identify the source.
  Either the URL changed or the feed is paywalled. Don't silently fix —
  surface it to Mickey and let him decide replace vs remove.
- **Curator returns invalid JSON.** Haiku occasionally wraps output in
  ```` ```json ```` despite instructions. The stripping logic in
  `curate.curate()` handles this, but if a new failure mode appears, log
  the raw response (`log.error("curator returned invalid JSON: %s\n---\n%s", ...)`)
  and don't add fallback parsing without seeing the actual output.
- **Email looks broken.** Open `digest_preview.html` from a `--dry-run` in
  a browser AND in Gmail (forward to yourself). Gmail strips lots of CSS;
  the template uses inline styles for that reason. If you "improve" the
  template, verify in Gmail before merging.
- **Turso connection errors.** The libsql client needs both
  `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`. The Worker uses the HTTP API
  directly (no client lib) — different code path, same credentials. The DB
  is named `newsagentdb` (not `news-agent`); use that with `turso db shell`.
- **No email some mornings.** If the curator returns 0 picks, `main.py`
  logs a warning and exits 1 — by design. A lean fresh-story day is not
  a regression. Investigate by checking the dedup line
  ("N total → M fresh (skipped K seen in last 7d)") and feed counts;
  if M is healthy but picks are 0, look at the curator prompt or
  profile, not the pipeline.

## Cost discipline

Mickey's ceiling is £15/month. Current run is ~£2/mo. Things that would
blow this up:
- Switching to Sonnet for *both* passes (curator + writer). One pass is OK,
  both is wasteful — the curator just picks IDs, Haiku is fine for that.
- Increasing `MAX_STORIES_PER_TOPIC` significantly (drives both input and
  output tokens up).
- Re-running the agent multiple times per day (it's designed to run once).
- Adding a vector DB (unnecessary at this scale; see `feedback.py` design
  note).

If you're proposing a change that increases LLM cost, estimate the new
monthly figure in the PR description.

## What NOT to do

- **Don't generalise.** This is a personal tool. Don't add multi-user
  support, don't parametrise the reader profile, don't extract a "framework."
- **Don't over-engineer the dedup.** URL-hash is fine. Don't add semantic
  similarity, fuzzy matching, or LSH unless duplicate-story complaints
  actually arrive.
- **Don't add a frontend.** Email is the interface. Vote tracking is the
  Worker. That's all the UI this needs.
- **Don't refactor `curate.py` for "cleanliness."** The prompts are the
  product. Restructuring the surrounding code without changing prompt
  behaviour is churn.
- **Don't auto-run the LLM during code review/iteration sessions.** Each
  full run costs ~£0.04 and Mickey will ask you to run it when he wants to.
  Use `--skip-llm` for fetch debugging.

## Current state / known gaps

- v1 live as of 2026-04-25. End-to-end send verified (Resend + Turso +
  Cloudflare Worker all wired up).
- Lenny's Newsletter feed returned 0 stories on the first live runs —
  likely a stale URL in `sources.yaml`. Worth a check next time Mickey
  is in there.
- Other RSS URLs in `sources.yaml` were best-guesses; most fetched fine
  on the first run, but watch for new failures and surface them rather
  than silently working around them.
- No Italian-language sources yet — Mickey is Italian, might want some
  (Calcio Italia, La Repubblica tech, etc.) but hasn't asked.
- No fixture/results auto-injection for Arsenal — currently relies on
  whatever the news feeds carry. A free football API (football-data.org)
  could feed match results directly into the breaking slot.
- The Brew-voice prompt is a starting point. Tune it monthly with concrete
  examples (save liked passages to a `voice_examples.md` and reference
  them in `WRITER_SYSTEM`).

## When in doubt

Ask Mickey. He'd rather answer a one-line question than review a 200-line
PR that went the wrong direction. This applies especially to:
- Source list changes (his taste, his call)
- Prompt rewrites (the voice is his voice)
- New topics or sections (changes the product)
- Anything that touches cost
