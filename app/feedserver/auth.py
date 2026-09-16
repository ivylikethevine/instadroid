"""FEED_TOKEN auth, signed media URLs, the cross-site refusal, and keeping tokens out of the access log."""

import hmac
import logging
import re
from base64 import b64decode
from collections.abc import Awaitable, Callable
from contextlib import suppress

from fastapi import Request, Response

from . import settings

OPEN_PATHS = ("/health",)  # the compose healthcheck calls it without credentials


class SkipHealthcheck(logging.Filter):
    """Drop GET /health (the compose healthcheck, every 30s) from uvicorn's access log, so the
    size-capped Docker log holds actual feed traffic rather than mostly healthchecks. Also blanks a
    ?token= value, which would otherwise be written to the log with the request path."""

    def filter(self, record: logging.LogRecord) -> bool:
        if "/health " in record.getMessage():
            return False
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_token(a) if isinstance(a, str) else a for a in record.args)
        return True


def _redact_token(text: str) -> str:
    return re.sub(r"([?&]token=)[^&\s]*", r"\1REDACTED", text)


def media_sig(file: str) -> str:
    """HMAC of one media path under FEED_TOKEN: lets a reader load that file, and nothing else."""
    return hmac.new(settings.FEED_TOKEN.encode(), f"media/{file}".encode(), "sha256").hexdigest()[:32]


def authorized(request: Request) -> bool:
    token: str = settings.FEED_TOKEN
    if not token or request.url.path in OPEN_PATHS:
        return True
    presented: list[str] = [request.query_params.get("token", "")]
    scheme: str
    _: str
    credentials: str
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        presented.append(credentials.strip())
    elif scheme.lower() == "basic":
        with suppress(ValueError):  # binascii.Error and UnicodeDecodeError are both ValueErrors
            presented.append(b64decode(credentials.strip(), validate=True).decode().partition(":")[2])
    if request.url.path.startswith("/media/"):
        file: str = request.url.path.removeprefix("/media/")
        if hmac.compare_digest(request.query_params.get("sig", ""), media_sig(file)):
            return True
    return any(p and hmac.compare_digest(p.encode(), token.encode()) for p in presented)


def cross_site(request: Request) -> bool:
    """A state-changing request a browser sent on another site's behalf: it carries an Origin that isn't
    this server's. Browsers always send Origin on cross-site POST/DELETE; curl and scripts send none. So a
    web page can't lock the scraper or trigger runs through a loopback server with no token."""
    origin: str | None = request.headers.get("origin")
    if request.method in ("GET", "HEAD", "OPTIONS") or not origin:
        return False
    own: set[str] = {settings.PUBLIC_URL, f"{request.url.scheme}://{request.url.netloc}"}
    return origin.rstrip("/") not in own


async def require_token(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """The HTTP middleware: refuse cross-site writes, then anything unauthorized."""
    if cross_site(request):
        return Response("cross-site request refused\n", status_code=403, media_type="text/plain")
    if authorized(request):
        return await call_next(request)
    return Response(
        "authentication required\n",
        status_code=401,
        media_type="text/plain",
        headers={"WWW-Authenticate": 'Basic realm="instadroid"'},
    )
