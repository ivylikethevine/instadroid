"""Serves the scraped posts as Atom feeds for FreshRSS. Runs as its own process (`uvicorn feedserver:app`)
and imports nothing from the scraper: the database is the only thing the two share.

/instagram.xml            -> everything
/instagram.xml?user=NAME  -> one account
/stories.xml              -> stored stories
/opml                     -> one-step bulk subscribe: every feed above, as an OPML outline
/media/<file>             -> cropped post images
/status                   -> plain-HTML health/status page
/health, /users           -> JSON (docs/openapi.json is the committed spec)
/control                  -> the manual lock and scrape-now requests

FEED_TOKEN (or FEED_TOKEN_FILE) turns on auth for everything except /health, see auth.authorized().

    settings   the environment, read once at import
    auth       the token, signed media URLs, the cross-site refusal, access-log redaction
    queries    every read of the database, and typed row accessors
    render     timestamps, ETags, media URLs and images, captions
    feeds      /instagram.xml, /stories.xml, /users, /opml
    control    /control
    status     /health, /status
"""

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import auth, control, feeds, settings, status

# uvicorn configures its loggers before importing this module, so a filter added here sticks.
logging.getLogger("uvicorn.access").addFilter(auth.SkipHealthcheck())

if not settings.FEED_TOKEN and settings.FEED_HOST not in ("127.0.0.1", "localhost", "::1"):
    logging.getLogger("uvicorn.error").warning(
        "FEED_HOST=%s without FEED_TOKEN: every feed and media file is readable by anyone who can reach it",
        settings.FEED_HOST,
    )

app = FastAPI(
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
app.include_router(status.router)
app.include_router(control.router)
