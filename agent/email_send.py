"""Resend wrapper. Handles Markdown → HTML and sending."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)


def _md_to_html(md: str) -> str:
    """Tiny Markdown → HTML. Enough for headings, bold, italic, links, sub tags.
    We deliberately don't use a heavy MD lib — the writer prompt controls output
    tightly so this can stay minimal and predictable."""
    html = md

    # headings
    html = re.sub(r"^### (.*)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.*)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^# (.*)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)

    # bold/italic
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)

    # links (preserve <sub>…</sub> we already generated in the prompt)
    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)

    # paragraph-ify: split on blank lines, wrap non-heading lines in <p>
    blocks = html.split("\n\n")
    wrapped: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if block.startswith("<h") or block.startswith("<ul") or block.startswith("<ol"):
            wrapped.append(block)
        else:
            # preserve single newlines as <br> within a paragraph
            wrapped.append(f"<p>{block.replace(chr(10), '<br>')}</p>")
    return "\n".join(wrapped)


def send_digest(markdown_body: str, dry_run: bool = False) -> None:
    template_dir = Path(__file__).parent.parent / "email_templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("digest.html")

    html_body = _md_to_html(markdown_body)
    date_str = datetime.now().strftime("%A, %-d %B")
    subject = f"Daily Digest — {date_str}"

    rendered = template.render(
        subject=subject,
        date_str=date_str,
        body_html=html_body,
    )

    if dry_run:
        out = Path("digest_preview.html")
        out.write_text(rendered, encoding="utf-8")
        log.info("dry-run: wrote %s", out.resolve())
        return

    resend.api_key = os.environ["RESEND_API_KEY"]
    resend.Emails.send({
        "from": os.environ["EMAIL_FROM"],
        "to": [os.environ["EMAIL_TO"]],
        "subject": subject,
        "html": rendered,
    })
    log.info("email sent to %s", os.environ["EMAIL_TO"])
