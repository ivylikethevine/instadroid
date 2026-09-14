"""Serves the scraped posts as Atom feeds for FreshRSS.

/instagram.xml            -> everything
/instagram.xml?user=NAME  -> one account
/stories.xml              -> stored stories
/opml                     -> one-step bulk subscribe: every feed above, as an OPML outline
/media/<file>             -> cropped post images
/status                   -> plain-HTML health/status page
"""

import logging
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from feedgen.feed import FeedGenerator
from PIL import Image

DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = os.environ.get("MEDIA_DIR", "/media")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
POLL_MAX_HOURS = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
MAX_LIMIT = 500


class _SkipHealthcheck(logging.Filter):
    """Drop GET /health (the compose healthcheck, every 30s) from uvicorn's access log, so the
    size-capped Docker log holds actual feed traffic rather than mostly healthchecks."""

    def filter(self, record):
        return "/health " not in record.getMessage()


# uvicorn configures its loggers before importing this module, so a filter added here sticks.
logging.getLogger("uvicorn.access").addFilter(_SkipHealthcheck())

app = FastAPI()
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def _query(sql: str, args=(), default=None, one: bool = False):
    """Run a read-only query and return its rows (or first row with `one`), or `default` when the
    database or table doesn't exist yet (the scraper creates both on its first start). Read-only so
    the scraper's writer lock never blocks a request."""
    if not Path(DB_PATH).exists():
        return default
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute(sql, args)
            return cur.fetchone() if one else cur.fetchall()
        except sqlite3.OperationalError:
            return default


def _col(row, name: str, default=None):
    """row[name], or `default` when a runs/posts row predates that column (sqlite3.Row has no get)."""
    keys = row.keys()  # sqlite3.Row's `in` checks values, not column names
    return row[name] if name in keys else default


def _limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def _utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _cached(request: Request, *parts) -> tuple[str, Response | None]:
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
    return f'<img src="{PUBLIC_URL}/media/{file}" alt=""{size} />'


def _thumbnail(fe, file: str) -> None:
    """Attach a Media RSS <media:thumbnail> (the full cover image, with its dimensions when known)
    for readers that show a picture in list view. Needs fg.load_extension("media")."""
    thumb = {"url": f"{PUBLIC_URL}/media/{file}"}
    if dims := _image_size(file):
        thumb |= {"width": str(dims[0]), "height": str(dims[1])}
    fe.media.thumbnail(thumb)


def _dt(value):
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


def rows(user: str | None, limit: int):
    where = " WHERE username = ?" if user else ""
    return _query(
        f"SELECT * FROM posts{where} ORDER BY COALESCE(posted_at, scraped_at) DESC LIMIT ?",
        (*([user] if user else []), _limit(limit)),
        default=[],
    )


def _media_rows(post_ids: list) -> dict:
    """{post_id: [extra slide filenames, in order]} for the given posts."""
    out: dict[str, list] = {}
    if post_ids:
        placeholders = ",".join("?" * len(post_ids))
        sql = f"SELECT post_id, file FROM media WHERE post_id IN ({placeholders}) ORDER BY post_id, idx"
        for post_id, file in _query(sql, post_ids, default=[]):
            out.setdefault(post_id, []).append(file)
    return out


def _avatar_files() -> dict:
    """{username: avatar_file} for every account with a captured avatar."""
    sql = "SELECT username, avatar_file FROM accounts WHERE avatar_file IS NOT NULL"
    return dict(map(tuple, _query(sql, default=[])))


def _feed_signal(user: str | None) -> tuple:
    """Cheap ETag input for /instagram.xml: post count and latest change (updated_at also moves when
    a row is merged in place), extra-slide count and latest avatar refresh, scoped to `user` when
    given so one account's new post doesn't invalidate every other per-account feed."""
    where, args = (" WHERE username = ?", (user,)) if user else ("", ())
    posts = _query(
        f"SELECT COUNT(*), COALESCE(MAX(COALESCE(updated_at, scraped_at)), '') FROM posts{where}",
        args,
        one=True,
    )
    media = _query("SELECT COUNT(*) FROM media", one=True)
    avatar = _query(f"SELECT COALESCE(MAX(avatar_updated_at), '') FROM accounts{where}", args, one=True)
    return (*(tuple(posts) if posts else (0, "")), media[0] if media else 0, avatar[0] if avatar else "")


def _post_count() -> int:
    row = _query("SELECT COUNT(*) FROM posts", one=True)
    return row[0] if row else 0


@app.get("/instagram.xml")
def feed(request: Request, user: str | None = None, limit: int = 200):
    etag, not_modified = _cached(request, user, limit, *_feed_signal(user))
    if not_modified:
        return not_modified
    fg = _new_feed(
        f"{PUBLIC_URL}/instagram.xml" + (f"?user={user}" if user else ""),
        f"Instagram — {user}" if user else "Instagram — Following",
    )

    entries = rows(user, limit)
    extra_slides = _media_rows([r["id"] for r in entries])
    avatars = _avatar_files()

    for r in entries:
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/post/{r['id']}")
        caption = r["caption"] or ""
        first_line = caption.split("\n", 1)[0][:90] or r["kind"] or "post"
        marker = "▶ " if r["kind"] == "video" else ""  # Reels and videos are stored as kind "video"
        fe.title(f"{marker}{r['username']}: {first_line}")
        url = _col(r, "url") or f"https://www.instagram.com/{r['username']}/"
        fe.link(href=url)
        fe.author(name=r["username"])
        fe.updated(_dt(r["scraped_at"]) or datetime.now(UTC))
        posted_abs = _dt(_col(r, "posted_at"))
        if posted_abs:
            fe.published(posted_abs)
        html = ""
        avatar = avatars.get(r["username"])
        if avatar:
            html += f'<p><img src="{PUBLIC_URL}/media/{avatar}" alt="" width="48" height="48" /></p>'
        for slide in ([r["media_file"]] if r["media_file"] else []) + extra_slides.get(r["id"], []):
            html += f"<p>{_img(slide)}</p>"
        if r["media_file"]:
            _thumbnail(fe, r["media_file"])
        html += f"<p>{escape(caption).replace(chr(10), '<br/>')}</p>"
        # Both dates are also on the entry itself (<published>/<updated>) for readers that sort by
        # those, but spelling them out here means sorting-by-eye works in any reader.
        meta = []
        if r["posted_date"]:
            meta.append(
                f"Posted {escape(r['posted_date'])}" + (f" ({_utc(posted_abs)})" if posted_abs else "")
            )
        elif posted_abs:
            meta.append(f"Posted {_utc(posted_abs)}")
        if place := _col(r, "place"):
            meta.append(f"at {escape(place)}")
        if scraped_abs := _dt(r["scraped_at"]):
            meta.append(f"saved {_utc(scraped_abs)}")
        meta.append(f'<a href="{escape(url)}">open on Instagram</a>')
        html += f"<p><small>{' · '.join(meta)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


def _story_rows(limit: int):
    """Stored stories, newest first (they share RETAIN_DAYS with posts; the scraper prunes them)."""
    return _query("SELECT * FROM stories ORDER BY scraped_at DESC LIMIT ?", (_limit(limit),), default=[])


def _stories_stats():
    """(count, latest scraped_at) - the ETag input for /stories.xml."""
    row = _query("SELECT COUNT(*), COALESCE(MAX(scraped_at), '') FROM stories", one=True)
    return tuple(row) if row else (0, "")


@app.get("/stories.xml")
def stories_feed(request: Request, limit: int = 200):
    etag, not_modified = _cached(request, limit, *_stories_stats())
    if not_modified:
        return not_modified
    fg = _new_feed(f"{PUBLIC_URL}/stories.xml", "Instagram — Stories")

    for r in _story_rows(limit):
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/story/{r['id']}")
        fe.title(f"{r['username']}'s story")
        fe.link(href=f"https://www.instagram.com/{r['username']}/")
        fe.author(name=r["username"])
        scraped = _dt(r["scraped_at"]) or datetime.now(UTC)
        fe.updated(scraped)
        fe.published(scraped)
        html = f"<p>{_img(r['media_file'])}</p>" if r["media_file"] else ""
        if r["media_file"]:
            _thumbnail(fe, r["media_file"])
        html += f"<p><small>saved {_utc(scraped)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


def _usernames() -> list:
    return [r[0] for r in _query("SELECT DISTINCT username FROM posts ORDER BY username", default=[])]


@app.get("/users")
def users():
    return _usernames()


@app.get("/opml")
def opml(request: Request):
    """One OPML outline nesting the aggregate feed, the stories feed, and one per-account feed
    per username in /users — a single FreshRSS import subscribes to everything this instance
    serves instead of pasting ?user= URLs in one at a time."""
    usernames = _usernames()
    etag, not_modified = _cached(request, PUBLIC_URL, *usernames)
    if not_modified:
        return not_modified

    root = Element("opml", version="2.0")
    head = SubElement(root, "head")
    SubElement(head, "title").text = "Instadroid"
    SubElement(head, "dateCreated").text = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    body = SubElement(root, "body")
    category = SubElement(body, "outline", text="Instagram", title="Instagram")

    def _feed_outline(parent, text, xml_url, html_url):
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
        category, "Instagram — Following", f"{PUBLIC_URL}/instagram.xml", "https://www.instagram.com/"
    )
    _feed_outline(category, "Instagram — Stories", f"{PUBLIC_URL}/stories.xml", "https://www.instagram.com/")
    for u in usernames:
        _feed_outline(
            category,
            u,
            f"{PUBLIC_URL}/instagram.xml?user={quote(u)}",
            f"https://www.instagram.com/{quote(u)}/",
        )

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode()
    return Response(xml_bytes, media_type="text/x-opml; charset=utf-8", headers={"ETag": etag})


def _scraper_health(now: datetime | None = None) -> tuple[str, str]:
    """("", "") when healthy, else (short label, reason). OVERDUE: no run has finished within
    POLL_MAX_HOURS + 30min of the last one, i.e. the loop itself looks stuck (a run still in
    progress fits comfortably inside that slack). FAILING: no *successful* run for two full poll
    cycles plus an hour, e.g. a login challenge nobody has answered yet. No runs at all is healthy —
    the scraper may simply be on its first one."""
    row = _query(
        "SELECT MAX(finished_at), MAX(CASE WHEN error IS NULL THEN finished_at END), MIN(started_at) FROM runs",
        one=True,
    )
    if not row:
        return "", ""
    last_finished, last_ok, first_started = row
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


@app.get("/health")
def health():
    count = _post_count()
    label, reason = _scraper_health()
    if label:
        # 503 so the compose healthcheck (which only checks for a 2xx) marks the container unhealthy.
        return JSONResponse({"ok": False, "posts": count, "reason": reason}, status_code=503)
    return {"ok": True, "posts": count}


def _user_counts():
    return _query(
        "SELECT username, COUNT(*) AS n, MAX(COALESCE(posted_at, scraped_at)) AS latest"
        " FROM posts GROUP BY username ORDER BY username",
        default=[],
    )


def _recent_runs(limit: int = 10):
    return _query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,), default=[])


def _latest_device():
    """Device props from the most recent run that got far enough to read them (a run that failed
    before connect_device() leaves these NULL). SELECT * so an old runs table still works."""
    return _query("SELECT * FROM runs WHERE android_release IS NOT NULL ORDER BY id DESC LIMIT 1", one=True)


def _short_error(error: str, limit: int = 140) -> str:
    """First line only, capped: some exceptions (e.g. a uiautomator2 server crash) embed a whole
    multi-KB Java stack trace in their own str(), which would otherwise blow up this table."""
    first_line = error.split("\n", 1)[0]
    return first_line[: limit - 1] + "…" if len(first_line) > limit else first_line


def _duration(run) -> str:
    start, end = _dt(run["started_at"]), _dt(run["finished_at"])
    if not start or not end:
        return "—"
    m, s = divmod(int((end - start).total_seconds()), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _link_failures(run) -> str:
    """ "sheet/clipboard" failure counts for a run, or "—" for a row from before these columns."""
    if _col(run, "link_sheet_failures", "—") == "—":
        return "—"
    return f"{run['link_sheet_failures'] or 0} sheet / {run['link_clipboard_failures'] or 0} clipboard"


def _run_count(run, col: str) -> str:
    """An integer runs column as text, or "—" for a row from before that column existed."""
    value = _col(run, col, "—")
    return "—" if value == "—" else str(value or 0)


def _run_memory(run) -> str:
    """redroid's peak memory during a run ("1843 MiB"), plus its OOM kills when there were any, or
    "—" when it wasn't measured (the cgroup wasn't readable, or a row from before these columns)."""
    peak, ooms = _col(run, "mem_peak_mb"), _col(run, "oom_kills")
    if peak is None:
        return "—"
    return f"{peak} MiB" + (f", {ooms} OOM kill(s)" if ooms else "")


def _run_text(run, col: str) -> str:
    """A text column off a runs row, or "" when it's NULL or the row predates that column."""
    return _col(run, col) or ""


def _run_result(run) -> tuple[str, str]:
    """(css class, text) for a run's Result cell: its error, else its warning, else "ok"."""
    if run["error"]:
        return "err", _short_error(run["error"])
    if warning := _run_text(run, "warning"):
        return "warn", f"warn: {_short_error(warning)}"
    return "", "ok"


@app.get("/status", response_class=HTMLResponse)
def status_page():
    users = _user_counts()
    total = sum(u["n"] for u in users)
    stories_count = _stories_stats()[0]
    runs = _recent_runs()
    device = _latest_device()
    latest = runs[0] if runs else None
    health_label, health_reason = _scraper_health()  # same verdict /health gives the healthcheck

    device_line = "no successful run yet"
    if device:
        device_line = f"Android {escape(device['android_release'] or '?')}"
        if device["android_sdk"]:
            device_line += f" (API {escape(device['android_sdk'])})"
        if device["device_product"]:
            device_line += f" — {escape(device['device_product'])}"
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
        if latest["error"] or health_label:
            status_word, status_class = ("ERROR" if latest["error"] else health_label), "bad"
        elif warning:
            status_word, status_class = "WARN", "warn"
        else:
            status_word, status_class = "OK", "good"
        latest_html = f"""
        <p><span class="badge {status_class}">{status_word}</span>
           finished {escape(latest["finished_at"])} ({_duration(latest)}),
           {latest["new_posts"] if latest["new_posts"] is not None else 0} new post(s)</p>
        {f'<p class="err">{escape(_short_error(latest["error"]))}</p>' if latest["error"] else ""}
        {f'<p class="err">{escape(health_reason)}</p>' if health_reason else ""}
        {f'<p class="warn">{escape(warning)}</p>' if warning and not latest["error"] else ""}
        """

    def _result_cell(r):
        css, text = _run_result(r)
        return f'<td class="{css}">{escape(text)}</td>'

    runs_rows = "".join(
        f"<tr><td>{escape(r['started_at'])}</td><td>{_duration(r)}</td>"
        f"<td>{r['new_posts'] if r['new_posts'] is not None else '—'}</td>"
        f"<td>{escape(_run_count(r, 'new_stories'))}</td>"
        f"<td>{escape(_run_count(r, 'filtered_posts'))}</td>"
        f"<td>{escape(_link_failures(r))}</td>"
        f"<td>{escape(_run_text(r, 'ig_version') or '—')}</td>"
        f"<td>{escape(_run_memory(r))}</td>"
        f"{_result_cell(r)}</tr>"
        for r in runs
    )
    users_rows = "".join(
        f"<tr><td>{escape(u['username'])}</td><td>{u['n']}</td><td>{escape(u['latest'] or '—')}</td></tr>"
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
