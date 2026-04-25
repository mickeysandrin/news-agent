# Daily News Agent

A personal news digest agent that lands in your inbox at 6am every morning.
Curates from your chosen RSS feeds across six topics (Tech/AI + PM, EdTech,
Arsenal + CL + World Cup, Cycling, Morning-Brew-style business), writes in
a punchy voice, and learns from 👍/👎 feedback over time.

## Stack

- **Runner**: GitHub Actions (free, scheduled)
- **LLM**: Claude Haiku 4.5 via the Anthropic API
- **Email**: Resend
- **DB** (for feedback + dedup): Turso (libSQL, free tier)
- **Feedback endpoint**: Cloudflare Worker (free tier)

Running cost: ~£2/month.

## Repo layout

```
news-agent/
├── agent/
│   ├── main.py            # entrypoint: fetch → curate → write → send
│   ├── fetch.py           # RSS + API fetchers
│   ├── dedup.py           # URL-hash dedup against Turso
│   ├── curate.py          # Claude prompts for curation + writing
│   ├── email_send.py      # Resend wrapper
│   ├── feedback.py        # builds preference profile from recent votes
│   └── sources.yaml       # source list per topic (edit freely)
├── email_templates/
│   └── digest.html        # Jinja2 template for the email
├── worker/
│   └── feedback-worker.js # Cloudflare Worker for vote tracking
├── .github/workflows/
│   └── daily.yml          # GitHub Actions schedule
├── requirements.txt
└── README.md
```

## Setup (one-time, ~45 mins)

### 1. Accounts you'll need

- **Anthropic API key** — https://console.anthropic.com (add a few £ of credit)
- **Resend account** — https://resend.com (free tier, 3k emails/month)
- **Turso account** — https://turso.tech (free tier)
- **Cloudflare account** — https://dash.cloudflare.com (free tier)
- **GitHub** — you already have this

### 2. Resend domain setup

Either:
- (Quick) Use Resend's onboarding sender — works immediately, emails come from
  `onboarding@resend.dev`
- (Better) Verify a domain you own so emails come from `news@yourdomain.com`.
  This meaningfully improves deliverability. Takes ~10 mins (DNS records).

### 3. Turso database

```bash
# install Turso CLI (macOS)
brew install tursodatabase/tap/turso

turso auth signup
turso db create news-agent
turso db show news-agent --url       # copy this → TURSO_DATABASE_URL
turso db tokens create news-agent    # copy this → TURSO_AUTH_TOKEN
```

Then create the schema:

```bash
turso db shell news-agent < schema.sql
```

`schema.sql` contents:

```sql
CREATE TABLE IF NOT EXISTS seen_urls (
  url_hash TEXT PRIMARY KEY,
  first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS votes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  story_id TEXT NOT NULL,
  story_title TEXT,
  story_source TEXT,
  story_topic TEXT,
  vote INTEGER NOT NULL,  -- 1 for up, -1 for down
  voted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_stories (
  story_id TEXT PRIMARY KEY,
  title TEXT,
  source TEXT,
  topic TEXT,
  url TEXT,
  sent_at TEXT NOT NULL
);
```

### 4. GitHub secrets

Repo → Settings → Secrets → Actions. Add:

- `ANTHROPIC_API_KEY`
- `RESEND_API_KEY`
- `TURSO_DATABASE_URL`
- `TURSO_AUTH_TOKEN`
- `EMAIL_TO` (your inbox)
- `EMAIL_FROM` (`news@yourdomain.com` or `onboarding@resend.dev`)
- `FEEDBACK_BASE_URL` (your Cloudflare Worker URL, see step 5)

### 5. Cloudflare Worker for feedback

```bash
npm install -g wrangler
cd worker
wrangler login
wrangler deploy
```

Put your Turso credentials in the Worker via `wrangler secret put TURSO_DATABASE_URL`
and `wrangler secret put TURSO_AUTH_TOKEN`.

Copy the deployed URL (something like `https://feedback-worker.yoursubdomain.workers.dev`)
and put it in the GitHub secret `FEEDBACK_BASE_URL`.

### 6. Test locally before scheduling

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export RESEND_API_KEY=...
# ... etc
python -m agent.main --dry-run   # writes email to local file, doesn't send
python -m agent.main             # actually sends
```

### 7. Turn on the schedule

The workflow in `.github/workflows/daily.yml` runs at 05:30 UTC (06:30 BST
in summer, 05:30 GMT in winter). Adjust the cron to taste — note GitHub
Actions cron is UTC only, so you may want to shift by an hour seasonally
or just accept the ±1hr drift.

## Build sequence (suggested)

- **Day 1–2**: Get v1 running — 3 sources, one topic, basic email. Land one
  in your inbox.
- **Day 3–5**: All sources, dedup, voice tuning.
- **Week 2**: Deploy the Worker, wire feedback.
- **Week 3**: Weekly preference-profile regeneration, polish the template.
- **Month 2+**: Tune the prompt monthly based on what the feedback loop is
  doing.

## Tuning knobs

- `sources.yaml` — add/remove feeds freely
- `agent/curate.py` → `SYSTEM_PROMPT` — adjust voice, section structure
- `agent/curate.py` → `MAX_STORIES_PER_TOPIC` — longer or shorter digest
- Switch `claude-haiku-4-5` to `claude-sonnet-4-6` in `curate.py` for
  higher-quality writing (~5x the cost, still well under budget)

## Known caveats

- **Arsenal match timing**: if they played last night, the curator should
  promote the result. There's a `breaking` hint in the prompt — tune to taste.
- **Morning Brew source**: treat as topic inspiration, not a copy target.
  The prompt explicitly instructs rewriting in your own words.
- **Cycling seasonality**: March–Oct is busy, Nov–Feb is quiet. The prompt
  allows sections to shrink gracefully.
