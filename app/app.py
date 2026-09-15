"""Serves the scraped posts as Atom feeds for FreshRSS.

/instagram.xml            -> everything
/instagram.xml?user=NAME  -> one account
/stories.xml              -> stored stories
/opml                     -> one-step bulk subscribe: every feed above, as an OPML outline
/media/<file>             -> cropped post images
/status                   -> plain-HTML health/status page
/health, /users           -> JSON (docs/openapi.json is the committed spec)

FEED_TOKEN (or FEED_TOKEN_FILE) turns on auth for everything except /health, see _authorized().
"""

import hmac
import logging
import os
import re
import sqlite3
import time
from base64 import b64decode
from collections.abc import Awaitable, Callable, Sequence
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator
from fileenv import env_secret
from PIL import Image
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = os.environ.get("MEDIA_DIR", "/media")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
POLL_MAX_HOURS = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
FEED_HOST = os.environ.get("FEED_HOST", "127.0.0.1")
# Required on every request except /health once set: as a bearer token, as the password of HTTP
# basic auth (any username), or as ?token=. Media URLs in the feeds are signed with it instead of
# carrying it, so a reader's image loads work without exposing the token itself. Empty = no auth.
FEED_TOKEN = env_secret("FEED_TOKEN")
MAX_LIMIT = 500
# The scraper's manual-control files (instadroid/control.py), read from the same environment.
CONTROL_DIR = Path(os.environ.get("CONTROL_DIR", "") or Path(DB_PATH).parent)
LOCK_MAX_HOURS = float(os.environ.get("LOCK_MAX_HOURS", "6"))
RUN_NOW_MIN_MINUTES = float(os.environ.get("RUN_NOW_MIN_MINUTES", "30"))
_OPEN_PATHS = ("/health",)  # the compose healthcheck calls it without credentials


class _SkipHealthcheck(logging.Filter):
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


# uvicorn configures its loggers before importing this module, so a filter added here sticks.
logging.getLogger("uvicorn.access").addFilter(_SkipHealthcheck())

if not FEED_TOKEN and FEED_HOST not in ("127.0.0.1", "localhost", "::1"):
    logging.getLogger("uvicorn.error").warning(
        "FEED_HOST=%s without FEED_TOKEN: every feed and media file is readable by anyone who can reach it",
        FEED_HOST,
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
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def _media_sig(file: str) -> str:
    """HMAC of one media path under FEED_TOKEN: lets a reader load that file, and nothing else."""
    return hmac.new(FEED_TOKEN.encode(), f"media/{file}".encode(), "sha256").hexdigest()[:32]


def _media_url(file: str) -> str:
    url = f"{PUBLIC_URL}/media/{file}"
    return f"{url}?sig={_media_sig(file)}" if FEED_TOKEN else url


def _feed_url(path: str, **params: str | None) -> str:
    """A feed URL for a subscription list (/opml), carrying the token when auth is on: whoever
    fetched the list already presented it, and the reader will need it for each feed."""
    query = {k: v for k, v in params.items() if v} | ({"token": FEED_TOKEN} if FEED_TOKEN else {})
    return f"{PUBLIC_URL}{path}" + (f"?{urlencode(query, quote_via=quote)}" if query else "")


def _authorized(request: Request) -> bool:
    if not FEED_TOKEN or request.url.path in _OPEN_PATHS:
        return True
    presented = [request.query_params.get("token", "")]
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        presented.append(credentials.strip())
    elif scheme.lower() == "basic":
        with suppress(ValueError):  # binascii.Error and UnicodeDecodeError are both ValueErrors
            presented.append(b64decode(credentials.strip(), validate=True).decode().partition(":")[2])
    if request.url.path.startswith("/media/"):
        file = request.url.path.removeprefix("/media/")
        if hmac.compare_digest(request.query_params.get("sig", ""), _media_sig(file)):
            return True
    return any(p and hmac.compare_digest(p.encode(), FEED_TOKEN.encode()) for p in presented)


def _cross_site(request: Request) -> bool:
    """A state-changing request a browser sent on another site's behalf: it carries an Origin that isn't
    this server's. Browsers always send Origin on cross-site POST/DELETE; curl and scripts send none. So a
    web page can't lock the scraper or trigger runs through a loopback server with no token."""
    origin = request.headers.get("origin")
    if request.method in ("GET", "HEAD", "OPTIONS") or not origin:
        return False
    own = {PUBLIC_URL, f"{request.url.scheme}://{request.url.netloc}"}
    return origin.rstrip("/") not in own


@app.middleware("http")
async def _require_token(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if _cross_site(request):
        return Response("cross-site request refused\n", status_code=403, media_type="text/plain")
    if _authorized(request):
        return await call_next(request)
    return Response(
        "authentication required\n",
        status_code=401,
        media_type="text/plain",
        headers={"WWW-Authenticate": 'Basic realm="instadroid"'},
    )


# Every value sqlite3 hands back (no converters are registered), and what an ETag is computed over.
type SqlValue = str | int | float | bytes | None
type EtagPart = SqlValue | tuple[SqlValue, ...]


def _query[T](sql: str, args: Sequence[SqlValue], fetch: Callable[[sqlite3.Cursor], T], default: T) -> T:
    """Run a read-only query and `fetch` from its cursor, or `default` when the database or table
    doesn't exist yet (the scraper creates both on its first start). Read-only so the scraper's writer
    lock never blocks a request."""
    if not Path(DB_PATH).exists():
        return default
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        try:
            return fetch(con.execute(sql, args))
        except sqlite3.OperationalError:
            return default


# typeshed types whatever a cursor or sqlite3.Row yields as Any, so every value read goes through one of
# these two narrowing functions (mapped over the cursor or row, never indexing it directly).
def _as_row(fetched: object) -> sqlite3.Row | None:
    return fetched if isinstance(fetched, sqlite3.Row) else None


def _as_sql_value(value: object) -> SqlValue:
    if value is None or isinstance(value, str | int | float | bytes):
        return value
    raise TypeError(f"unexpected sqlite value {value!r}")


def _all_rows(sql: str, args: Sequence[SqlValue] = ()) -> list[sqlite3.Row]:
    """Every row of a read-only query, or none before the database or table exists."""
    return _query(sql, args, lambda cur: [row for row in map(_as_row, cur) if row is not None], [])


def _one_row(sql: str, args: Sequence[SqlValue] = ()) -> sqlite3.Row | None:
    """The first row of a read-only query, or None (also before the database or table exists)."""
    return _query(sql, args, lambda cur: next(map(_as_row, cur), None), None)


def _values(row: sqlite3.Row) -> tuple[SqlValue, ...]:
    """Every value of a row, in column order (what tuple(row) gives)."""
    return tuple(map(_as_sql_value, row))


def _value(row: sqlite3.Row, key: str | int) -> SqlValue:
    """row[key]: by position, or by column name (ValueError for a column the row doesn't have)."""
    return _values(row)[key if isinstance(key, int) else row.keys().index(key)]


def _str(row: sqlite3.Row, key: str | int) -> str:
    """row[key] as it reads in an f-string (so NULL is "None"), for a NOT NULL column."""
    return str(_value(row, key))


def _text(row: sqlite3.Row, key: str | int) -> str | None:
    """row[key] as text, or None for NULL."""
    value = _value(row, key)
    return value if value is None or isinstance(value, str) else str(value)


def _int(row: sqlite3.Row, key: str | int) -> int | None:
    """An INTEGER column (or COUNT) of a row, or None for NULL."""
    value = _value(row, key)
    if value is None or isinstance(value, int):
        return value
    raise TypeError(f"expected an integer in column {key!r}, got {value!r}")


def _has(row: sqlite3.Row, name: str) -> bool:
    """Whether a row has column `name`: False for a runs/posts row that predates it."""
    columns = row.keys()  # sqlite3.Row's `in` checks values, not column names
    return name in columns


def _col_text(row: sqlite3.Row, name: str) -> str | None:
    """A text column, or None when it's NULL or the row predates that column (sqlite3.Row has no get)."""
    return _text(row, name) if _has(row, name) else None


def _col_int(row: sqlite3.Row, name: str) -> int | None:
    """An integer column, or None when it's NULL or the row predates that column."""
    return _int(row, name) if _has(row, name) else None


def _limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def _utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _cached(request: Request, *parts: EtagPart) -> tuple[str, Response | None]:
    """(ETag over `parts`, a 304 response if the client already has it, else None)."""
    etag = f'"{sha256("|".join(map(str, parts)).encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return etag, Response(status_code=304, headers={"ETag": etag})
    return etag, None


def _new_feed(feed_id: str, title: str) -> FeedGenerator:
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
    path = Path(MEDIA_DIR) / file
    try:
        st = path.stat()
    except OSError:
        return None
    return _image_size_cached(str(path), st.st_mtime_ns, st.st_size)


def _img(file: str) -> str:
    """An <img> for a stored media file. width/height let a reader reserve the space before the
    image loads; the inline max-width/height:auto keeps a reader that honours those attributes but
    narrows the column from stretching it."""
    dims = _image_size(file)
    size = f' width="{dims[0]}" height="{dims[1]}" style="max-width:100%;height:auto"' if dims else ""
    return f'<img src="{_media_url(file)}" alt=""{size} />'


def _thumbnail(fe: FeedEntry, file: str) -> None:
    """Attach a Media RSS <media:thumbnail> (the full cover image, with its dimensions when known)
    for readers that show a picture in list view. Needs fg.load_extension("media"), which adds
    `fe.media` at runtime (_new_feed() always loads it)."""
    thumb = {"url": _media_url(file)}
    if dims := _image_size(file):
        thumb |= {"width": str(dims[0]), "height": str(dims[1])}
    fe.media.thumbnail(thumb)


def _dt(value: str | None) -> datetime | None:
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


# Instagram usernames: letters, digits, "_" and ".", at most 30, never ending in "." (so a mention
# at the end of a sentence doesn't swallow the full stop). Hashtags: letters, digits and "_", not all
# digits. Neither may follow a word character, "/" or "@"/"#", which leaves emails and URL
# fragments ("a@b.com", "example.com/#top") alone.
_CAPTION_LINK = re.compile(
    r"(?<![\w/@#.])@(?P<user>[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)(?![\w@])"
    r"|(?<![\w/@#&])#(?P<tag>\w*[^\W\d]\w*)"
)


def _caption_html(caption: str) -> str:
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


def rows(user: str | None, limit: int) -> list[sqlite3.Row]:
    where = " WHERE username = ?" if user else ""
    return _all_rows(
        f"SELECT * FROM posts{where} ORDER BY COALESCE(posted_at, scraped_at) DESC LIMIT ?",
        (*([user] if user else []), _limit(limit)),
    )


def _media_rows(post_ids: list[str]) -> dict[str, list[str]]:
    """{post_id: [extra slide filenames, in order]} for the given posts."""
    out: dict[str, list[str]] = {}
    if post_ids:
        placeholders = ",".join("?" * len(post_ids))
        sql = f"SELECT post_id, file FROM media WHERE post_id IN ({placeholders}) ORDER BY post_id, idx"
        for row in _all_rows(sql, post_ids):
            out.setdefault(_str(row, 0), []).append(_str(row, 1))
    return out


def _avatar_files() -> dict[str, str]:
    """{username: avatar_file} for every account with a captured avatar."""
    sql = "SELECT username, avatar_file FROM accounts WHERE avatar_file IS NOT NULL"
    return {_str(row, 0): _str(row, 1) for row in _all_rows(sql)}


def _feed_signal(user: str | None) -> tuple[EtagPart, ...]:
    """Cheap ETag input for /instagram.xml: post count and latest change (updated_at also moves when
    a row is merged in place), extra-slide count and latest avatar refresh, scoped to `user` when
    given so one account's new post doesn't invalidate every other per-account feed."""
    where, args = (" WHERE username = ?", (user,)) if user else ("", ())
    posts = _one_row(
        f"SELECT COUNT(*), COALESCE(MAX(COALESCE(updated_at, scraped_at)), '') FROM posts{where}", args
    )
    media = _one_row("SELECT COUNT(*) FROM media")
    avatar = _one_row(f"SELECT COALESCE(MAX(avatar_updated_at), '') FROM accounts{where}", args)
    alerts = tuple(_values(a) for a in _open_alerts()) if not user else ()
    return (
        *(_values(posts) if posts else (0, "")),
        _value(media, 0) if media else 0,
        _value(avatar, 0) if avatar else "",
        *alerts,
    )


def _open_alerts() -> list[sqlite3.Row]:
    """The scraper's open failure alerts (instadroid/alerts.py), oldest first; none before that table exists."""
    return _all_rows("SELECT kind, message, raised_at FROM alerts ORDER BY raised_at")


def _post_count() -> int:
    row = _one_row("SELECT COUNT(*) FROM posts")
    return (_int(row, 0) or 0) if row else 0


class Health(BaseModel):
    ok: bool
    posts: int
    reason: str | None = None


# responses= for the Atom feed routes, written out on each: FastAPI types that parameter as
# dict[int | str, dict[str, Any]], which only a literal in place matches without an Any of our own.
@app.get(
    "/instagram.xml",
    response_class=Response,
    responses={200: {"content": {"application/atom+xml": {}}, "description": "An Atom feed."}, 304: {}},
    summary="Posts feed",
)
def feed(request: Request, user: str | None = None, limit: int = 200) -> Response:
    """Stored posts as Atom, newest first. `user` narrows it to one account; `limit` is capped at
    500."""
    etag, not_modified = _cached(request, user, limit, *_feed_signal(user))
    if not_modified:
        return not_modified
    fg = _new_feed(
        f"{PUBLIC_URL}/instagram.xml" + (f"?user={user}" if user else ""),
        f"Instagram — {user}" if user else "Instagram — Following",
    )

    entries = rows(user, limit)
    extra_slides = _media_rows([_str(r, "id") for r in entries])
    avatars = _avatar_files()

    # Open failure alerts go first in the aggregate feed, where a reader is already looking. Each gets
    # a new id per raise, so a reader shows it again if it's resolved and raised later.
    for alert in [] if user else _open_alerts():
        message = _str(alert, "message")
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/alert/{_str(alert, 'kind')}/{_str(alert, 'raised_at')}")
        fe.title(f"⚠ instadroid needs attention: {message[:90]}")
        fe.link(href=f"{PUBLIC_URL}/status")
        raised = _dt(_text(alert, "raised_at")) or datetime.now(UTC)
        fe.updated(raised)
        fe.published(raised)
        fe.content(
            f"<p>{escape(message)}</p><p><small>since {_utc(raised)} · "
            f'<a href="{PUBLIC_URL}/status">status page</a></small></p>',
            type="html",
        )

    for r in entries:
        username, kind, media_file = _str(r, "username"), _text(r, "kind"), _text(r, "media_file")
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/post/{_str(r, 'id')}")
        caption = _text(r, "caption") or ""
        first_line = caption.split("\n", 1)[0][:90] or kind or "post"
        marker = "▶ " if kind == "video" else ""  # Reels and videos are stored as kind "video"
        fe.title(f"{marker}{username}: {first_line}")
        url = _col_text(r, "url") or f"https://www.instagram.com/{username}/"
        fe.link(href=url)
        fe.author(name=username)
        fe.updated(_dt(_text(r, "scraped_at")) or datetime.now(UTC))
        posted_abs = _dt(_col_text(r, "posted_at"))
        if posted_abs:
            fe.published(posted_abs)
        html = ""
        avatar = avatars.get(username)
        if avatar:
            html += f'<p><img src="{_media_url(avatar)}" alt="" width="48" height="48" /></p>'
        for slide in ([media_file] if media_file else []) + extra_slides.get(_str(r, "id"), []):
            html += f"<p>{_img(slide)}</p>"
        if media_file:
            _thumbnail(fe, media_file)
        html += f"<p>{_caption_html(caption)}</p>"
        # Both dates are also on the entry itself (<published>/<updated>) for readers that sort by
        # those, but spelling them out here means sorting-by-eye works in any reader.
        meta: list[str] = []
        if posted_date := _text(r, "posted_date"):
            meta.append(f"Posted {escape(posted_date)}" + (f" ({_utc(posted_abs)})" if posted_abs else ""))
        elif posted_abs:
            meta.append(f"Posted {_utc(posted_abs)}")
        if place := _col_text(r, "place"):
            meta.append(f"at {escape(place)}")
        if scraped_abs := _dt(_text(r, "scraped_at")):
            meta.append(f"saved {_utc(scraped_abs)}")
        meta.append(f'<a href="{escape(url)}">open on Instagram</a>')
        html += f"<p><small>{' · '.join(meta)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


def _story_rows(limit: int) -> list[sqlite3.Row]:
    """Stored stories, newest first (they share RETAIN_DAYS with posts; the scraper prunes them)."""
    return _all_rows("SELECT * FROM stories ORDER BY scraped_at DESC LIMIT ?", (_limit(limit),))


def _stories_stats() -> tuple[int, str]:
    """(count, latest scraped_at) - the ETag input for /stories.xml."""
    row = _one_row("SELECT COUNT(*), COALESCE(MAX(scraped_at), '') FROM stories")
    return (_int(row, 0) or 0, _str(row, 1)) if row else (0, "")


@app.get(
    "/stories.xml",
    response_class=Response,
    responses={200: {"content": {"application/atom+xml": {}}, "description": "An Atom feed."}, 304: {}},
    summary="Stories feed",
)
def stories_feed(request: Request, limit: int = 200) -> Response:
    """Stored story frames as Atom, newest first; `limit` is capped at 500."""
    etag, not_modified = _cached(request, limit, *_stories_stats())
    if not_modified:
        return not_modified
    fg = _new_feed(f"{PUBLIC_URL}/stories.xml", "Instagram — Stories")

    for r in _story_rows(limit):
        username, media_file = _str(r, "username"), _text(r, "media_file")
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/story/{_str(r, 'id')}")
        fe.title(f"{username}'s story")
        fe.link(href=f"https://www.instagram.com/{username}/")
        fe.author(name=username)
        scraped = _dt(_text(r, "scraped_at")) or datetime.now(UTC)
        fe.updated(scraped)
        fe.published(scraped)
        html = f"<p>{_img(media_file)}</p>" if media_file else ""
        if media_file:
            _thumbnail(fe, media_file)
        html += f"<p><small>saved {_utc(scraped)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


def _usernames() -> list[str]:
    return [_str(r, 0) for r in _all_rows("SELECT DISTINCT username FROM posts ORDER BY username")]


@app.get("/users", summary="Accounts with stored posts")
def users() -> list[str]:
    """Every username with at least one stored post, sorted."""
    return _usernames()


@app.get(
    "/opml",
    response_class=Response,
    responses={200: {"content": {"text/x-opml": {}}, "description": "An OPML outline."}, 304: {}},
    summary="Subscription list",
)
def opml(request: Request) -> Response:
    """One OPML outline nesting the aggregate feed, the stories feed, and one per-account feed
    per username in /users — a single FreshRSS import subscribes to everything this instance
    serves instead of pasting ?user= URLs in one at a time."""
    usernames = _usernames()
    etag, not_modified = _cached(request, PUBLIC_URL, FEED_TOKEN, *usernames)
    if not_modified:
        return not_modified

    root = Element("opml", version="2.0")
    head = SubElement(root, "head")
    SubElement(head, "title").text = "Instadroid"
    SubElement(head, "dateCreated").text = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    body = SubElement(root, "body")
    category = SubElement(body, "outline", text="Instagram", title="Instagram")

    def _feed_outline(parent: Element, text: str, xml_url: str, html_url: str) -> None:
        SubElement(
            parent,
            "outline",
            type="rss",
            text=text,
            title=text,
            xmlUrl=xml_url,
            htmlUrl=html_url,
        )

    _feed_outline(
        category, "Instagram — Following", _feed_url("/instagram.xml"), "https://www.instagram.com/"
    )
    _feed_outline(category, "Instagram — Stories", _feed_url("/stories.xml"), "https://www.instagram.com/")
    for u in usernames:
        _feed_outline(
            category, u, _feed_url("/instagram.xml", user=u), f"https://www.instagram.com/{quote(u)}/"
        )

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode()
    return Response(xml_bytes, media_type="text/x-opml; charset=utf-8", headers={"ETag": etag})


def _scraper_health(now: datetime | None = None) -> tuple[str, str]:
    """("", "") when healthy, else (short label, reason). OVERDUE: no run has finished within
    POLL_MAX_HOURS + 30min of the last one, i.e. the loop itself looks stuck (a run still in
    progress fits comfortably inside that slack). FAILING: no *successful* run for two full poll
    cycles plus an hour, e.g. a login challenge nobody has answered yet. No runs at all is healthy —
    the scraper may simply be on its first one."""
    row = _one_row(
        "SELECT MAX(finished_at), MAX(CASE WHEN error IS NULL THEN finished_at END), MIN(started_at) FROM runs"
    )
    if not row:
        return "", ""
    last_finished, last_ok, first_started = _text(row, 0), _text(row, 1), _text(row, 2)
    now = now or datetime.now(UTC)
    finished = _dt(last_finished)
    if finished and now - finished > timedelta(hours=POLL_MAX_HOURS + 0.5):
        hours = (now - finished).total_seconds() / 3600
        return "OVERDUE", f"no scrape run has finished in {hours:.1f}h"
    reference = _dt(last_ok) or _dt(first_started)
    if reference and now - reference > timedelta(hours=2 * POLL_MAX_HOURS + 1):
        hours = (now - reference).total_seconds() / 3600
        return "FAILING", f"no successful scrape run in {hours:.1f}h"
    return "", ""


@app.get(
    "/health",
    response_model=Health,
    response_model_exclude_none=True,
    responses={503: {"model": Health, "description": "Scraping looks stuck or keeps failing."}},
    summary="Scraper health",
)
def health() -> Health | JSONResponse:
    """`ok` unless no run has finished for too long or none has succeeded for too long (then 503,
    with a `reason`). Never needs the feed token."""
    count = _post_count()
    label, reason = _scraper_health()
    if label:
        # 503 so the compose healthcheck (which only checks for a 2xx) marks the container unhealthy.
        return JSONResponse({"ok": False, "posts": count, "reason": reason}, status_code=503)
    return Health(ok=True, posts=count)


def _user_counts() -> list[sqlite3.Row]:
    return _all_rows(
        "SELECT username, COUNT(*) AS n, MAX(COALESCE(posted_at, scraped_at)) AS latest"
        " FROM posts GROUP BY username ORDER BY username"
    )


def _recent_runs(limit: int = 10) -> list[sqlite3.Row]:
    return _all_rows("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


def _latest_device() -> sqlite3.Row | None:
    """Device props from the most recent run that got far enough to read them (a run that failed
    before connect_device() leaves these NULL). SELECT * so an old runs table still works."""
    return _one_row("SELECT * FROM runs WHERE android_release IS NOT NULL ORDER BY id DESC LIMIT 1")


def _short_error(error: str, limit: int = 140) -> str:
    """First line only, capped: some exceptions (e.g. a uiautomator2 server crash) embed a whole
    multi-KB Java stack trace in their own str(), which would otherwise blow up this table."""
    first_line = error.split("\n", 1)[0]
    return first_line[: limit - 1] + "…" if len(first_line) > limit else first_line


def _duration(run: sqlite3.Row) -> str:
    start, end = _dt(_text(run, "started_at")), _dt(_text(run, "finished_at"))
    if not start or not end:
        return "—"
    m, s = divmod(int((end - start).total_seconds()), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _link_failures(run: sqlite3.Row) -> str:
    """ "sheet/clipboard" failure counts for a run, or "—" for a row from before these columns."""
    if not _has(run, "link_sheet_failures"):
        return "—"
    return f"{_int(run, 'link_sheet_failures') or 0} sheet / {_int(run, 'link_clipboard_failures') or 0} clipboard"


def _run_count(run: sqlite3.Row, col: str) -> str:
    """An integer runs column as text, or "—" for a row from before that column existed."""
    return str(_int(run, col) or 0) if _has(run, col) else "—"


def _run_memory(run: sqlite3.Row) -> str:
    """redroid's peak memory during a run ("1843 MiB"), plus its OOM kills when there were any, or
    "—" when it wasn't measured (the cgroup wasn't readable, or a row from before these columns)."""
    peak, ooms = _col_int(run, "mem_peak_mb"), _col_int(run, "oom_kills")
    if peak is None:
        return "—"
    return f"{peak} MiB" + (f", {ooms} OOM kill(s)" if ooms else "")


def _run_text(run: sqlite3.Row, col: str) -> str:
    """A text column off a runs row, or "" when it's NULL or the row predates that column."""
    return _col_text(run, col) or ""


def _run_result(run: sqlite3.Row) -> tuple[str, str]:
    """(css class, text) for a run's Result cell: its error, else its warning, else "ok"."""
    if error := _text(run, "error"):
        return "err", _short_error(error)
    if warning := _run_text(run, "warning"):
        return "warn", f"warn: {_short_error(warning)}"
    return "", "ok"


class ControlState(BaseModel):
    locked: bool  # scheduled runs are held
    scrape_now: bool  # a scrape-now request is waiting for the poll loop


def _control_state() -> ControlState:
    lock = CONTROL_DIR / "manual.lock"
    try:
        age = time.time() - lock.stat().st_mtime
        locked = LOCK_MAX_HOURS <= 0 or age < LOCK_MAX_HOURS * 3600
    except OSError:
        locked = False
    return ControlState(locked=locked, scrape_now=(CONTROL_DIR / "scrape-now").exists())


@app.get("/control", summary="Manual control state")
def control_state() -> ControlState:
    """Whether the manual lock holds scheduled runs, and whether a scrape-now request is pending."""
    return _control_state()


@app.post("/control/lock", summary="Hold scheduled runs")
def lock() -> ControlState:
    """Stop the scraper starting runs while the device is driven by hand. A run already going finishes.
    The lock is ignored once it's LOCK_MAX_HOURS old."""
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    (CONTROL_DIR / "manual.lock").touch()
    return _control_state()


@app.delete("/control/lock", summary="Release the lock")
def unlock() -> ControlState:
    (CONTROL_DIR / "manual.lock").unlink(missing_ok=True)
    return _control_state()


@app.post(
    "/control/scrape-now",
    status_code=202,
    summary="Ask for a run now",
    responses={409: {"description": "The manual lock is in place."}, 429: {"description": "Too soon."}},
)
def scrape_now() -> ControlState:
    """Ask the poll loop to start a run within about 30 seconds instead of waiting out its sleep.
    Refused while locked, and within RUN_NOW_MIN_MINUTES of the last run finishing."""
    if _control_state().locked:
        raise HTTPException(409, "the manual lock is in place; release it first")
    row = _one_row("SELECT MAX(finished_at) FROM runs")
    if row and (finished := _dt(_text(row, 0))):
        since = (datetime.now(UTC) - finished).total_seconds() / 60
        if since < RUN_NOW_MIN_MINUTES:
            raise HTTPException(
                429,
                f"the last run finished {since:.0f} min ago; try again in {RUN_NOW_MIN_MINUTES - since:.0f} min",
            )
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    (CONTROL_DIR / "scrape-now").touch()
    return _control_state()


@app.get("/status", response_class=HTMLResponse, summary="Status page")
def status_page() -> HTMLResponse:
    """A plain-HTML page of recent runs, the device, and per-account totals."""
    users = _user_counts()
    total = sum(_int(u, "n") or 0 for u in users)
    stories_count = _stories_stats()[0]
    runs = _recent_runs()
    device = _latest_device()
    latest = runs[0] if runs else None
    health_label, health_reason = _scraper_health()  # same verdict /health gives the healthcheck

    device_line = "no successful run yet"
    if device:
        device_line = f"Android {escape(_text(device, 'android_release') or '?')}"
        if sdk := _text(device, "android_sdk"):
            device_line += f" (API {escape(sdk)})"
        if product := _text(device, "device_product"):
            device_line += f" — {escape(product)}"
        if ig := _run_text(device, "ig_version"):
            device_line += f" · Instagram {escape(ig)}"
        if profile := _run_text(device, "selector_profile"):
            device_line += f" (profile {escape(profile)})"
        if image := _run_text(device, "redroid_image"):
            device_line += f" · {escape(image)}"

    if not latest:
        latest_html = "<p>No scrape runs recorded yet.</p>"
    else:
        warning = _run_text(latest, "warning")
        error = _text(latest, "error")
        new_posts = _int(latest, "new_posts")
        if error or health_label:
            status_word, status_class = ("ERROR" if error else health_label), "bad"
        elif warning:
            status_word, status_class = "WARN", "warn"
        else:
            status_word, status_class = "OK", "good"
        latest_html = f"""
        <p><span class="badge {status_class}">{status_word}</span>
           finished {escape(_str(latest, "finished_at"))} ({_duration(latest)}),
           {new_posts if new_posts is not None else 0} new post(s)</p>
        {f'<p class="err">{escape(_short_error(error))}</p>' if error else ""}
        {f'<p class="err">{escape(health_reason)}</p>' if health_reason else ""}
        {f'<p class="warn">{escape(warning)}</p>' if warning and not error else ""}
        """
    latest_html += "".join(
        f'<p class="err">Alert since {escape(_str(a, "raised_at")[:16])}: {escape(_str(a, "message"))}</p>'
        for a in _open_alerts()
    )
    control = _control_state()
    if control.locked:
        latest_html += '<p class="warn">Manual lock in place: scheduled runs are held.</p>'
    if control.scrape_now:
        latest_html += "<p>A scrape-now request is waiting for the poll loop.</p>"

    def _result_cell(r: sqlite3.Row) -> str:
        css, text = _run_result(r)
        return f'<td class="{css}">{escape(text)}</td>'

    def _new_posts_cell(r: sqlite3.Row) -> str:
        new_posts = _int(r, "new_posts")
        return f"<td>{new_posts if new_posts is not None else '—'}</td>"

    runs_rows = "".join(
        f"<tr><td>{escape(_str(r, 'started_at'))}</td><td>{_duration(r)}</td>"
        f"{_new_posts_cell(r)}"
        f"<td>{escape(_run_count(r, 'new_stories'))}</td>"
        f"<td>{escape(_run_count(r, 'filtered_posts'))}</td>"
        f"<td>{escape(_link_failures(r))}</td>"
        f"<td>{escape(_run_text(r, 'ig_version') or '—')}</td>"
        f"<td>{escape(_run_memory(r))}</td>"
        f"{_result_cell(r)}</tr>"
        for r in runs
    )
    users_rows = "".join(
        f"<tr><td>{escape(_str(u, 'username'))}</td><td>{_str(u, 'n')}</td>"
        f"<td>{escape(_text(u, 'latest') or '—')}</td></tr>"
        for u in users
    )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="60">
<title>Instadroid status</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem;
        background: #fafafa; color: #222; }}
h1 {{ font-size: 1.3rem; }} h2 {{ font-size: 1rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ text-align: left; padding: 0.25rem 0.6rem; border-bottom: 1px solid #ddd; font-size: 0.9rem; }}
.badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 0.3rem; font-weight: 600; }}
.badge.good {{ background: #d7f5da; color: #146c2e; }}
.badge.bad {{ background: #f8d7da; color: #842029; }}
.badge.warn {{ background: #fff3cd; color: #664d03; }}
.err {{ color: #842029; }}
.warn {{ color: #664d03; }}
</style></head>
<body>
<h1>Instadroid status</h1>
<p>{device_line}</p>
<h2>Last scrape</h2>
{latest_html}
<h2>Recent runs</h2>
<table><tr><th>Started</th><th>Duration</th><th>New</th><th>New stories</th><th>Filtered</th><th>Link fails</th><th>Instagram</th><th>Peak mem</th><th>Result</th></tr>{runs_rows or '<tr><td colspan="9">none</td></tr>'}</table>
<h2>Totals</h2>
<p>{total} post(s) stored across {len(users)} account(s), {stories_count} stor{"y" if stories_count == 1 else "ies"} stored</p>
<table><tr><th>Account</th><th>Posts</th><th>Latest</th></tr>{users_rows or '<tr><td colspan="3">none</td></tr>'}</table>
</body></html>"""
    return HTMLResponse(html)
