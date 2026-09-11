"""Serves the scraped posts as Atom feeds for FreshRSS.

/instagram.xml            -> everything
/instagram.xml?user=NAME  -> one account
/stories.xml              -> currently unexpired stories
/opml                     -> one-step bulk subscribe: every feed above, as an OPML outline
/media/<file>             -> cropped post images
/status                   -> plain-HTML health/status page
"""

import logging
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from feedgen.feed import FeedGenerator

DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = os.environ.get("MEDIA_DIR", "/media")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
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


def _connect():
    # The compose mount is already read-only; open read-only here too so a lock held by the
    # driver's writer never blocks a request.
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


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
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        q = "SELECT * FROM posts"
        args: list = []
        if user:
            q += " WHERE username = ?"
            args.append(user)
        q += " ORDER BY COALESCE(posted_at, scraped_at) DESC LIMIT ?"
        args.append(max(1, min(limit, MAX_LIMIT)))
        try:
            return con.execute(q, args).fetchall()
        except sqlite3.OperationalError:
            return []  # e.g. the driver hasn't run db_init() yet and the table doesn't exist


def _media_rows(post_ids: list) -> dict:
    """{post_id: [extra slide filenames, in order]} for the given posts. Empty (not an error) if
    the media table doesn't exist yet — a post's cover in media_file still renders on its own."""
    if not post_ids:
        return {}
    with closing(_connect()) as con:
        placeholders = ",".join("?" * len(post_ids))
        try:
            out: dict[str, list] = {}
            for post_id, file in con.execute(
                f"SELECT post_id, file FROM media WHERE post_id IN ({placeholders}) ORDER BY post_id, idx",
                post_ids,
            ):
                out.setdefault(post_id, []).append(file)
            return out
        except sqlite3.OperationalError:
            return {}


def _avatar_files() -> dict:
    """{username: avatar_file}. Empty if the accounts table doesn't exist yet or has no avatars."""
    with closing(_connect()) as con:
        try:
            return dict(
                con.execute("SELECT username, avatar_file FROM accounts WHERE avatar_file IS NOT NULL")
            )
        except sqlite3.OperationalError:
            return {}


def _media_accounts_signal():
    """(media row count, latest avatar refresh) - extra ETag input alongside _stats() so a newly
    captured carousel slide or avatar invalidates a cached feed even when the post count and
    updated_at haven't moved. Same defensive OperationalError handling as _stats()."""
    if not Path(DB_PATH).exists():
        return 0, ""
    with closing(_connect()) as con:
        try:
            media_count = con.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        except sqlite3.OperationalError:
            media_count = 0
        try:
            latest_avatar = con.execute(
                "SELECT COALESCE(MAX(avatar_updated_at), '') FROM accounts"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            latest_avatar = ""
        return media_count, latest_avatar


def _stats():
    """(post count, latest change) - cheap aggregate used for /health and the feed's ETag, so a
    poll that hasn't seen new data doesn't cost a full row scan or feed render. updated_at also
    moves when an existing row is merged/edited in place (e.g. a placeholder caption gets
    replaced once the real one renders), which scraped_at alone would miss."""
    if not Path(DB_PATH).exists():
        return 0, ""
    with closing(_connect()) as con:
        try:
            return con.execute(
                "SELECT COUNT(*), COALESCE(MAX(COALESCE(updated_at, scraped_at)), '') FROM posts"
            ).fetchone()
        except sqlite3.OperationalError:
            pass  # e.g. driver hasn't added the updated_at column to this table yet
        try:
            return con.execute("SELECT COUNT(*), COALESCE(MAX(scraped_at), '') FROM posts").fetchone()
        except sqlite3.OperationalError:
            return 0, ""  # e.g. the driver hasn't run db_init() yet and the table doesn't exist


@app.get("/instagram.xml")
def feed(request: Request, user: str | None = None, limit: int = 200):
    count, latest = _stats()
    media_count, latest_avatar = _media_accounts_signal()
    etag_input = f"{user}|{limit}|{count}|{latest}|{media_count}|{latest_avatar}"
    etag = f'"{sha256(etag_input.encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    fg = FeedGenerator()
    title = f"Instagram — {user}" if user else "Instagram — Following"
    fg.id(f"{PUBLIC_URL}/instagram.xml" + (f"?user={user}" if user else ""))
    fg.title(title)
    fg.link(href=fg.id(), rel="self")
    fg.updated(datetime.now(UTC))

    entries = rows(user, limit)
    extra_slides = _media_rows([r["id"] for r in entries])
    avatars = _avatar_files()

    for r in entries:
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/post/{r['id']}")
        caption = r["caption"] or ""
        first_line = caption.split("\n", 1)[0][:90] or r["kind"] or "post"
        fe.title(f"{r['username']}: {first_line}")
        keys = r.keys()  # sqlite3.Row has no __contains__
        url = r["url"] if "url" in keys and r["url"] else f"https://www.instagram.com/{r['username']}/"
        fe.link(href=url)
        fe.author(name=r["username"])
        fe.updated(_dt(r["scraped_at"]) or datetime.now(UTC))
        if "posted_at" in keys and _dt(r["posted_at"]):
            fe.published(_dt(r["posted_at"]))
        html = ""
        avatar = avatars.get(r["username"])
        if avatar:
            html += f'<p><img src="{PUBLIC_URL}/media/{avatar}" alt="" width="48" height="48" /></p>'
        for slide in ([r["media_file"]] if r["media_file"] else []) + extra_slides.get(r["id"], []):
            html += f'<p><img src="{PUBLIC_URL}/media/{slide}" alt="" /></p>'
        html += f"<p>{escape(caption).replace(chr(10), '<br/>')}</p>"
        # Both dates are also on the entry itself (<published>/<updated>) for readers that sort by
        # those, but spelling them out here means sorting-by-eye works in any reader.
        posted_abs = _dt(r["posted_at"]) if "posted_at" in keys else None
        if r["posted_date"]:
            posted_line = f"Posted {escape(r['posted_date'])}"
            if posted_abs:
                posted_line += f" ({posted_abs.strftime('%Y-%m-%d %H:%M UTC')})"
            meta = [posted_line]
        elif posted_abs:
            meta = [f"Posted {posted_abs.strftime('%Y-%m-%d %H:%M UTC')}"]
        else:
            meta = []
        if "place" in keys and r["place"]:
            meta.append(f"at {escape(r['place'])}")
        scraped_abs = _dt(r["scraped_at"])
        if scraped_abs:
            meta.append(f"saved {scraped_abs.strftime('%Y-%m-%d %H:%M UTC')}")
        meta.append(f'<a href="{escape(url)}">open on Instagram</a>')
        html += f"<p><small>{' · '.join(meta)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


def _story_rows(limit: int):
    """Unexpired stories, newest first. Empty (not an error) if the stories table doesn't exist
    yet — same defensive shape as rows()."""
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT * FROM stories WHERE expires_at > ? ORDER BY scraped_at DESC LIMIT ?",
                (datetime.now(UTC).isoformat(), max(1, min(limit, MAX_LIMIT))),
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def _stories_stats():
    """(count, latest scraped_at) among unexpired stories - the ETag input for /stories.xml. A
    story expiring between requests must also change the ETag, which count-and-latest alone
    wouldn't catch (nothing about an expired row's own fields changes) - see the etag_input below,
    which folds in "how many are unexpired right now" rather than just an ever-growing max."""
    if not Path(DB_PATH).exists():
        return 0, ""
    with closing(_connect()) as con:
        try:
            return con.execute(
                "SELECT COUNT(*), COALESCE(MAX(scraped_at), '') FROM stories WHERE expires_at > ?",
                (datetime.now(UTC).isoformat(),),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0, ""


@app.get("/stories.xml")
def stories_feed(request: Request, limit: int = 200):
    count, latest = _stories_stats()
    etag = f'"{sha256(f"{limit}|{count}|{latest}".encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    fg = FeedGenerator()
    fg.id(f"{PUBLIC_URL}/stories.xml")
    fg.title("Instagram — Stories")
    fg.link(href=fg.id(), rel="self")
    fg.updated(datetime.now(UTC))

    for r in _story_rows(limit):
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/story/{r['id']}")
        fe.title(f"{r['username']}'s story")
        fe.link(href=f"https://www.instagram.com/{r['username']}/")
        fe.author(name=r["username"])
        scraped = _dt(r["scraped_at"]) or datetime.now(UTC)
        fe.updated(scraped)
        fe.published(scraped)
        html = f'<p><img src="{PUBLIC_URL}/media/{r["media_file"]}" alt="" /></p>' if r["media_file"] else ""
        expires = _dt(r["expires_at"])
        meta = [f"saved {scraped.strftime('%Y-%m-%d %H:%M UTC')}"]
        if expires:
            meta.append(f"expires {expires.strftime('%Y-%m-%d %H:%M UTC')}")
        html += f"<p><small>{' · '.join(meta)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


def _usernames() -> list:
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        try:
            return [r[0] for r in con.execute("SELECT DISTINCT username FROM posts ORDER BY username")]
        except sqlite3.OperationalError:
            return []


@app.get("/users")
def users():
    return _usernames()


@app.get("/opml")
def opml(request: Request):
    """One OPML outline nesting the aggregate feed, the stories feed, and one per-account feed
    per username in /users — a single FreshRSS import subscribes to everything this instance
    serves instead of pasting ?user= URLs in one at a time."""
    usernames = _usernames()
    etag = f'"{sha256(f"{PUBLIC_URL}|{'|'.join(usernames)}".encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

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
    if not Path(DB_PATH).exists():
        return "", ""
    with closing(_connect()) as con:
        try:
            last_finished, last_ok, first_started = con.execute(
                "SELECT MAX(finished_at), MAX(CASE WHEN error IS NULL THEN finished_at END), MIN(started_at)"
                " FROM runs"
            ).fetchone()
        except sqlite3.OperationalError:
            return "", ""  # e.g. the driver hasn't run db_init() yet and the table doesn't exist
    now = now or datetime.now(UTC)
    poll_max_h = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
    finished = _dt(last_finished)
    if finished and now - finished > timedelta(hours=poll_max_h + 0.5):
        hours = (now - finished).total_seconds() / 3600
        return "OVERDUE", f"no scrape run has finished in {hours:.1f}h"
    reference = _dt(last_ok) or _dt(first_started)
    if reference and now - reference > timedelta(hours=2 * poll_max_h + 1):
        hours = (now - reference).total_seconds() / 3600
        return "FAILING", f"no successful scrape run in {hours:.1f}h"
    return "", ""


@app.get("/health")
def health():
    count, _ = _stats()
    label, reason = _scraper_health()
    if label:
        # 503 so the compose healthcheck (which only checks for a 2xx) marks the container unhealthy.
        return JSONResponse({"ok": False, "posts": count, "reason": reason}, status_code=503)
    return {"ok": True, "posts": count}


def _user_counts():
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT username, COUNT(*) AS n, MAX(COALESCE(posted_at, scraped_at)) AS latest"
                " FROM posts GROUP BY username ORDER BY username"
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def _recent_runs(limit: int = 10):
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        try:
            return con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        except sqlite3.OperationalError:
            return []  # e.g. the driver hasn't run db_init() yet and the table doesn't exist


def _latest_device():
    """Device props from the most recent run that got far enough to read them (a run that failed
    before connect_device() leaves these NULL)."""
    if not Path(DB_PATH).exists():
        return None
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        try:
            # SELECT *, not named columns: a runs table from before ig_version/redroid_image
            # existed must still show its Android version rather than fail the whole query.
            return con.execute(
                "SELECT * FROM runs WHERE android_release IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None


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
    """ "sheet/clipboard" failure counts for a run, or "—" against a runs row from before these
    columns existed (SELECT * omits columns the table doesn't have, rather than nulling them)."""
    keys = run.keys()
    if "link_sheet_failures" not in keys and "link_clipboard_failures" not in keys:
        return "—"
    sheet = run["link_sheet_failures"] if "link_sheet_failures" in keys else None
    clip = run["link_clipboard_failures"] if "link_clipboard_failures" in keys else None
    return f"{sheet or 0} sheet / {clip or 0} clipboard"


def _run_new_stories(run) -> str:
    """new_stories for a run, or "—" against a runs row from before that column existed."""
    keys = run.keys()
    if "new_stories" not in keys:
        return "—"
    return str(run["new_stories"] or 0)


def _run_text(run, col: str) -> str:
    """A text column off a runs row, or "" when it's NULL or the row predates that column."""
    keys = run.keys()  # sqlite3.Row has no __contains__
    return (run[col] or "") if col in keys else ""


def _run_warning(run) -> str:
    """A run's warning (e.g. it stopped early on empty screens), or ""."""
    return _run_text(run, "warning")


def _run_result(run) -> tuple[str, str]:
    """(css class, text) for a run's Result cell: its error, else its warning, else "ok"."""
    if run["error"]:
        return "err", _short_error(run["error"])
    if warning := _run_warning(run):
        return "warn", f"warn: {_short_error(warning)}"
    return "", "ok"


def _active_stories_count() -> int:
    if not Path(DB_PATH).exists():
        return 0
    with closing(_connect()) as con:
        try:
            return con.execute(
                "SELECT COUNT(*) FROM stories WHERE expires_at > ?", (datetime.now(UTC).isoformat(),)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


@app.get("/status", response_class=HTMLResponse)
def status_page():
    total, _ = _stats()
    active_stories = _active_stories_count()
    users = _user_counts()
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
        if image := _run_text(device, "redroid_image"):
            device_line += f" · {escape(image)}"

    if not latest:
        latest_html = "<p>No scrape runs recorded yet.</p>"
    else:
        warning = _run_warning(latest)
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
        f"<td>{escape(_run_new_stories(r))}</td>"
        f"<td>{escape(_link_failures(r))}</td>"
        f"<td>{escape(_run_text(r, 'ig_version') or '—')}</td>"
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
<table><tr><th>Started</th><th>Duration</th><th>New</th><th>New stories</th><th>Link fails</th><th>Instagram</th><th>Result</th></tr>{runs_rows or '<tr><td colspan="7">none</td></tr>'}</table>
<h2>Totals</h2>
<p>{total} post(s) stored across {len(users)} account(s), {active_stories} active stor{"y" if active_stories == 1 else "ies"}</p>
<table><tr><th>Account</th><th>Posts</th><th>Latest</th></tr>{users_rows or '<tr><td colspan="3">none</td></tr>'}</table>
</body></html>"""
    return HTMLResponse(html)
