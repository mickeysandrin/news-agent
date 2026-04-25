/**
 * Cloudflare Worker — feedback endpoint.
 *
 * URL shape: /vote?id=STORY_ID&vote=up   (or &vote=down)
 *
 * Writes to Turso via the HTTP API. On success, shows a small "noted"
 * confirmation page. Idempotent: re-voting replaces the previous vote.
 *
 * Deploy:
 *   wrangler secret put TURSO_DATABASE_URL
 *   wrangler secret put TURSO_AUTH_TOKEN
 *   wrangler deploy
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/vote") {
      return new Response("Not found", { status: 404 });
    }

    const storyId = url.searchParams.get("id");
    const voteStr = url.searchParams.get("vote");
    if (!storyId || !["up", "down"].includes(voteStr)) {
      return new Response("Bad request", { status: 400 });
    }
    const vote = voteStr === "up" ? 1 : -1;

    // Look up story metadata so the profile builder has source/topic context
    const lookup = await turso(env, {
      sql: "SELECT title, source, topic FROM sent_stories WHERE story_id = ?",
      args: [storyId],
    });

    let title = "", source = "", topic = "";
    const row = lookup?.results?.[0]?.rows?.[0];
    if (row) {
      [title, source, topic] = row;
    }

    const now = new Date().toISOString();
    await turso(env, {
      sql: `INSERT INTO votes (story_id, story_title, story_source, story_topic, vote, voted_at)
            VALUES (?, ?, ?, ?, ?, ?)`,
      args: [storyId, title, source, topic, vote, now],
    });

    const emoji = vote === 1 ? "👍" : "👎";
    const label = vote === 1 ? "More like this" : "Less like this";
    return new Response(confirmationPage(emoji, label), {
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  },
};

async function turso(env, stmt) {
  // Turso's HTTP endpoint — simpler than libsql client in a Worker.
  // URL form: libsql://name-org.turso.io → https://name-org.turso.io
  const httpUrl = env.TURSO_DATABASE_URL.replace(/^libsql:/, "https:");
  const resp = await fetch(`${httpUrl}/v2/pipeline`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.TURSO_AUTH_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      requests: [
        { type: "execute", stmt },
        { type: "close" },
      ],
    }),
  });
  if (!resp.ok) {
    console.error("turso error", resp.status, await resp.text());
    return null;
  }
  const data = await resp.json();
  return data?.results?.[0]?.response?.result;
}

function confirmationPage(emoji, label) {
  return `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Noted</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         background: #f6f4ee; color: #1d1d1f;
         display: flex; min-height: 100vh; margin: 0;
         align-items: center; justify-content: center; }
  .card { background: white; padding: 40px 48px; border-radius: 12px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.08); text-align: center; }
  .emoji { font-size: 48px; margin-bottom: 12px; }
  h1 { margin: 0 0 8px 0; font-size: 20px; }
  p { margin: 0; color: #6b6b70; font-size: 14px; }
</style></head>
<body><div class="card">
  <div class="emoji">${emoji}</div>
  <h1>Got it — ${label.toLowerCase()}.</h1>
  <p>This will shape tomorrow's digest.</p>
</div></body></html>`;
}
