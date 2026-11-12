"""Serves the scraped posts as Atom feeds for FreshRSS. Runs as its own process (`uvicorn feedserver:app`)
and imports nothing from the scraper: the database is the only thing the two share.

/instagram.xml            -> everything
/instagram.xml?user=NAME  -> one account (also ?kind=, ?since=, ?q=)
/stories.xml              -> stored stories
/instagram.json, /stories.json -> the same, as JSON Feed 1.1
/opml                     -> one-step bulk subscribe: the per-account feeds, as an OPML outline
/media/<file>             -> cropped post images
/status                   -> plain-HTML health/status page
/subscribe                -> plain-HTML page of the URLs to give a reader
/browse                   -> plain-HTML account list, timelines (?user=) and caption search (?q=)
/health, /users           -> JSON (docs/openapi.json is the committed spec)
/control                  -> the manual lock and scrape-now requests

FEED_TOKEN (or FEED_TOKEN_FILE) turns on auth for everything except /health, see auth.authorized().

    settings   the environment, read once at import
    auth       the token, signed media URLs, the cross-site refusal, access-log redaction
    queries    every read of the database, and typed row accessors
    render     timestamps, ETags, media URLs and images, captions
    entries    what each post, story and alert says, for either feed format
    feeds      /instagram.xml and .json, /stories.xml and .json, /users, /opml
    browse     /browse
    control    /control
    status     /health, /status
    subscribe  /subscribe
    freshrss   `python -m feedserver.freshrss`: the file a FreshRSS import follows /opml from
"""

import logging
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import auth, browse, control, feeds, settings, status, subscribe

# uvicorn configures its loggers before importing this module, so a filter added here sticks.
logging.getLogger("uvicorn.access").addFilter(auth.SkipHealthcheck())

if not settings.FEED_TOKEN and settings.FEED_HOST not in ("127.0.0.1", "localhost", "::1"):
    logging.getLogger("uvicorn.error").warning(
        "FEED_HOST=%s without FEED_TOKEN: every feed and media file is readable by anyone who can reach it",
        settings.FEED_HOST,
    )
if not settings.PUBLIC_URL.startswith(("http://", "https://")):
    # A reader keeps the feed URLs /opml writes, which start with a PUBLIC_URL that is set; FreshRSS's
    # OPML parser drops an outline whose xmlUrl has no scheme, and its fetcher refuses any URL that
    # isn't http(s).
    logging.getLogger("uvicorn.error").warning(
        "PUBLIC_URL=%s has no http:// or https:// scheme: readers will reject the feed URLs in /opml",
        settings.PUBLIC_URL,
    )
elif urlsplit(settings.PUBLIC_URL).hostname == "localhost" and settings.FEED_HOST == "127.0.0.1":
    # A reader in a container can resolve localhost to ::1 first, which this server doesn't bind.
    logging.getLogger("uvicorn.error").warning(
        "PUBLIC_URL=%s names localhost while FEED_HOST binds 127.0.0.1 only: a reader that resolves"
        " localhost to ::1 fails to fetch; set PUBLIC_URL=http://127.0.0.1:<port> instead",
        settings.PUBLIC_URL,
    )

app: FastAPI = FastAPI(
    title="instadroid",
    summary="Atom feeds of an Instagram Following feed scraped from a real Android app.",
    description=(
        "When the server runs with `FEED_TOKEN`, every path except `/health` needs that token: as "
        "`Authorization: Bearer <token>`, as the password of HTTP basic auth, or as `?token=`. "
        "`/media/<file>` URLs written into the feeds carry a per-file `sig` instead."
    ),
)
settings.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.MEDIA_DIR), name="media")
app.middleware("http")(auth.require_token)
app.include_router(feeds.router)
app.include_router(browse.router)
app.include_router(status.router)
app.include_router(subscribe.router)
app.include_router(control.router)
