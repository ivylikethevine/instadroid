"""/subscribe: the URLs to hand a feed reader, spelled for the address this request came in on, and
where each one goes in FreshRSS."""

import sqlite3
from html import escape
from ipaddress import ip_address
from urllib.parse import SplitResult, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from shared.pagestyle import PAGE_STYLE

from . import queries, render, settings

router: APIRouter = APIRouter()

LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "::1", "localhost")

_STYLE: str = (
    PAGE_STYLE
    + """h2 { margin-top: 2rem; }
code { background: #eee; padding: 0.1rem 0.3rem; overflow-wrap: anywhere; }
.warn { color: #664d03; }
"""
)


def allow_entry(base: str) -> str | None:
    """`base` as an entry of FreshRSS's INTERNAL_HOST_ALLOWLIST (`host:port`, the scheme's port when the
    URL names none), or None for a public IP address, which FreshRSS fetches without one. A hostname
    gets an entry: only FreshRSS knows what it resolves to there."""
    parts: SplitResult = urlsplit(base)
    host: str = parts.hostname or ""
    try:
        if ip_address(host).is_global:
            return None
    except ValueError:
        pass
    port: int = parts.port or (443 if parts.scheme == "https" else 80)
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _warnings(base: str, ready: bool) -> str:
    """What would make a subscription fail right now, each as a paragraph."""
    html: str = ""
    if not ready:
        html += (
            '<p class="warn">The scraper has not created its database yet: the list of account feeds'
            " answers 503 until its first start.</p>"
        )
    if urlsplit(base).hostname in LOOPBACK_HOSTS:
        html += (
            '<p class="warn">These URLs name this host\'s loopback address, so only a reader on this host'
            " that shares its network (the compose <code>freshrss</code> service) can fetch them. For a"
            " reader anywhere else, open this page at the address that reader will use"
        )
        if settings.FEED_HOST in LOOPBACK_HOSTS:
            html += (
                "; the server listens on loopback only, so set <code>FEED_HOST=0.0.0.0</code> and a"
                " <code>FEED_TOKEN</code> first"
            )
        html += ".</p>"
    elif not settings.PUBLIC_URL_SET:
        html += (
            f'<p class="warn">Images in the feeds are still linked from <code>{escape(settings.PUBLIC_URL)}'
            f"</code>, which only this host can load. Set <code>PUBLIC_URL={escape(base)}</code> in"
            " <code>.env</code> and restart the app, so they load wherever the feeds are read.</p>"
        )
    return html


@router.get("/subscribe", response_class=HTMLResponse, summary="How to subscribe a reader")
def subscribe(request: Request) -> HTMLResponse:
    """A plain-HTML page of the feed URLs and the subscription list's URL, with the token when auth is
    on, and where each goes in FreshRSS. They start with PUBLIC_URL when that is set, else with the
    address this request came in on."""
    con: sqlite3.Connection
    with queries.connection() as con:
        ready: bool = queries.has_posts_table(con)
    base: str = render.feed_base(request)
    posts: str = escape(render.feed_url(base, "/instagram.xml"))
    stories: str = escape(render.feed_url(base, "/stories.xml"))
    opml: str = escape(render.feed_url(base, "/opml"))
    download: str = escape(render.feed_url("", "/opml"))
    entry: str | None = allow_entry(base)
    allow: str = (
        "<h2>If FreshRSS fetches nothing</h2>"
        "<p>FreshRSS 1.30 and later refuses a loopback or private address that is not on its allow"
        f" list. Add <code>{escape(entry)}</code> to its <code>INTERNAL_HOST_ALLOWLIST</code> environment"
        " variable, or under <em>System configuration</em>. The compose <code>freshrss</code> service"
        " already allows 127.0.0.1 and localhost on the feed port.</p>"
        if entry
        else ""
    )
    return HTMLResponse(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Subscribe</title><style>{_STYLE}</style></head>
<body><p><a href="/browse">All accounts</a> · <a href="/status">Status</a></p>
<h1>Subscribe a reader</h1>
{_warnings(base, ready)}
<p>Pick one of the two: subscribed to both, every post shows up twice.</p>
<h2>Everything in one feed</h2>
<p>Add this as a feed. In FreshRSS: <em>Subscription management</em>, <em>Add a feed or category</em>,
<em>Add a feed</em>.</p>
<p><code>{posts}</code></p>
<p>Stories are a feed of their own: <code>{stories}</code></p>
<h2>One feed per account</h2>
<p>This URL is a list of feeds, not a feed, so <em>Add a feed</em> rejects it. In FreshRSS:
<em>Subscription management</em>, <em>Add a feed or category</em>, <em>Add dynamic OPML</em>; name the
category and paste this as the <em>OPML URL</em>. FreshRSS then follows the list as accounts come and go.
It holds the stories feed too.</p>
<p><code>{opml}</code></p>
<p>Or <a href="{download}" download="instadroid.opml">download the list</a> and import the file.
FreshRSS keeps following a list it imported; another reader needs a new import when accounts change.</p>
{allow}
</body></html>"""
    )
