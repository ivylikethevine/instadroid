"""What the feeds and the status page are built from: timestamps, ETags, media URLs and images."""

import os
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import Request, Response
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator
from PIL import Image, ImageFile

from . import auth, settings
from .queries import EtagPart


def local(dt: datetime) -> str:
    """`dt` in the TZ setting's zone, named by its abbreviation: "2026-09-08 01:00 PDT"."""
    return dt.astimezone(settings.ZONE).strftime("%Y-%m-%d %H:%M %Z")


def cached(request: Request, *parts: EtagPart) -> tuple[str, Response | None]:
    """(ETag over `parts`, a 304 response if the client already has it, else None)."""
    etag: str = f'"{sha256("|".join(map(str, parts)).encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return etag, Response(status_code=304, headers={"ETag": etag})
    return etag, None


def media_url(file: str) -> str:
    url: str = f"{settings.PUBLIC_URL}/media/{file}"
    return f"{url}?sig={auth.media_sig(file)}" if settings.FEED_TOKEN else url


def feed_url(path: str, *, token: bool = True, **params: str | None) -> str:
    """A feed URL for a subscription list (/opml), carrying the token when auth is on: whoever
    fetched the list already presented it, and the reader will need it for each feed. `token=False`
    leaves it out, for a feed's link to itself."""
    secret: str = settings.FEED_TOKEN if token else ""
    query: dict[str, str] = {k: v for k, v in params.items() if v} | ({"token": secret} if secret else {})
    return f"{settings.PUBLIC_URL}{path}" + (f"?{urlencode(query, quote_via=quote)}" if query else "")


def new_feed(feed_id: str, title: str) -> FeedGenerator:
    fg: FeedGenerator = FeedGenerator()
    fg.id(feed_id)
    fg.title(title)
    fg.link(href=feed_id, rel="self")
    fg.updated(datetime.now(UTC))
    fg.load_extension("media")
    return fg


@lru_cache(maxsize=4096)
def _image_size_cached(path: str, mtime_ns: int, size: int) -> tuple[int, int] | None:
    """(width, height) from the image header only. Keyed on mtime/size too, so a re-captured avatar
    (same name, new file) isn't served its old dimensions."""
    im: ImageFile.ImageFile
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:  # truncated, or not an image at all
        return None


def _image_size(file: str) -> tuple[int, int] | None:
    """Dimensions of a stored media file (relative to MEDIA_DIR), or None if it's missing."""
    path: Path = settings.MEDIA_DIR / file
    st: os.stat_result
    try:
        st = path.stat()
    except OSError:
        return None
    return _image_size_cached(str(path), st.st_mtime_ns, st.st_size)


def img(file: str) -> str:
    """An <img> for a stored media file. width/height let a reader reserve the space before the
    image loads; the inline max-width/height:auto keeps a reader that honours those attributes but
    narrows the column from stretching it."""
    dims: tuple[int, int] | None = _image_size(file)
    size: str = f' width="{dims[0]}" height="{dims[1]}" style="max-width:100%;height:auto"' if dims else ""
    return f'<img src="{media_url(file)}" alt=""{size} />'


def thumbnail(fe: FeedEntry, file: str) -> None:
    """Attach a Media RSS <media:thumbnail> (the full cover image, with its dimensions when known)
    for readers that show a picture in list view. Needs fg.load_extension("media"), which adds
    `fe.media` at runtime (new_feed() always loads it)."""
    thumb: dict[str, str] = {"url": media_url(file)}
    dims: tuple[int, int] | None
    if dims := _image_size(file):
        thumb |= {"width": str(dims[0]), "height": str(dims[1])}
    fe.media.thumbnail(thumb)
