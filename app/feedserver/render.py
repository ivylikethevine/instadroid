"""What the feeds and the status page are built from: timestamps, ETags, media URLs and images, captions."""

import re
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import Request, Response
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator
from PIL import Image

from . import auth, settings
from .queries import EtagPart


def utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def parse_dt(value: str | None) -> datetime | None:
    """Parse a stored ISO timestamp, tolerant of a missing/malformed value and of a naive one
    (feedgen rejects those) — returns None rather than raising, so one bad row can't 500 the
    whole feed."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def cached(request: Request, *parts: EtagPart) -> tuple[str, Response | None]:
    """(ETag over `parts`, a 304 response if the client already has it, else None)."""
    etag = f'"{sha256("|".join(map(str, parts)).encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return etag, Response(status_code=304, headers={"ETag": etag})
    return etag, None


def media_url(file: str) -> str:
    url = f"{settings.PUBLIC_URL}/media/{file}"
    return f"{url}?sig={auth.media_sig(file)}" if settings.FEED_TOKEN else url


def feed_url(path: str, **params: str | None) -> str:
    """A feed URL for a subscription list (/opml), carrying the token when auth is on: whoever
    fetched the list already presented it, and the reader will need it for each feed."""
    token = settings.FEED_TOKEN
    query = {k: v for k, v in params.items() if v} | ({"token": token} if token else {})
    return f"{settings.PUBLIC_URL}{path}" + (f"?{urlencode(query, quote_via=quote)}" if query else "")


def new_feed(feed_id: str, title: str) -> FeedGenerator:
    fg = FeedGenerator()
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
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:  # truncated, or not an image at all
        return None


def _image_size(file: str) -> tuple[int, int] | None:
    """Dimensions of a stored media file (relative to MEDIA_DIR), or None if it's missing."""
    path = Path(settings.MEDIA_DIR) / file
    try:
        st = path.stat()
    except OSError:
        return None
    return _image_size_cached(str(path), st.st_mtime_ns, st.st_size)


def img(file: str) -> str:
    """An <img> for a stored media file. width/height let a reader reserve the space before the
    image loads; the inline max-width/height:auto keeps a reader that honours those attributes but
    narrows the column from stretching it."""
    dims = _image_size(file)
    size = f' width="{dims[0]}" height="{dims[1]}" style="max-width:100%;height:auto"' if dims else ""
    return f'<img src="{media_url(file)}" alt=""{size} />'


def thumbnail(fe: FeedEntry, file: str) -> None:
    """Attach a Media RSS <media:thumbnail> (the full cover image, with its dimensions when known)
    for readers that show a picture in list view. Needs fg.load_extension("media"), which adds
    `fe.media` at runtime (new_feed() always loads it)."""
    thumb = {"url": media_url(file)}
    if dims := _image_size(file):
        thumb |= {"width": str(dims[0]), "height": str(dims[1])}
    fe.media.thumbnail(thumb)


# Instagram usernames: letters, digits, "_" and ".", at most 30, never ending in "." (so a mention
# at the end of a sentence doesn't swallow the full stop). Hashtags: letters, digits and "_", not all
# digits. Neither may follow a word character, "/" or "@"/"#", which leaves emails and URL
# fragments ("a@b.com", "example.com/#top") alone.
_CAPTION_LINK = re.compile(
    r"(?<![\w/@#.])@(?P<user>[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)(?![\w@])"
    r"|(?<![\w/@#&])#(?P<tag>\w*[^\W\d]\w*)"
)


def caption_html(caption: str) -> str:
    """A caption as HTML: escaped, line breaks kept, and each @mention and #hashtag linked to its
    Instagram page. Matching runs on the raw text, so an escaped entity like &#x27; is never
    mistaken for a hashtag."""
    out: list[str] = []
    pos = 0
    for m in _CAPTION_LINK.finditer(caption):
        out.append(escape(caption[pos : m.start()]))
        if user := m["user"]:
            href = f"https://www.instagram.com/{quote(user)}/"
        else:
            href = f"https://www.instagram.com/explore/tags/{quote(m['tag'])}/"
        out.append(f'<a href="{escape(href)}">{escape(m[0])}</a>')
        pos = m.end()
    out.append(escape(caption[pos:]))
    return "".join(out).replace("\n", "<br/>")
