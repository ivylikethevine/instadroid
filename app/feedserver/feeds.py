"""The subscription routes: /instagram.xml, /stories.xml, /users and /opml."""

import sqlite3
from datetime import UTC, datetime
from html import escape
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, Request, Response
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator
from shared.sqlrows import opt_str
from shared.timestamps import parse_iso

from . import queries, render, settings
from .queries import string, text

router: APIRouter = APIRouter()


# responses= for the Atom feed routes, written out on each: FastAPI types that parameter as
# dict[int | str, dict[str, Any]], which only a literal in place matches without an Any of our own.
@router.get(
    "/instagram.xml",
    response_class=Response,
    responses={200: {"content": {"application/atom+xml": {}}, "description": "An Atom feed."}, 304: {}},
    summary="Posts feed",
)
def feed(request: Request, user: str | None = None, limit: int = 200) -> Response:
    """Stored posts as Atom, newest first. `user` narrows it to one account; `limit` is capped at
    500."""
    con: sqlite3.Connection
    etag: str
    not_modified: Response | None
    with queries.connection() as con:
        # Open failure alerts go first in the aggregate feed, where a reader is already looking.
        alerts: list[sqlite3.Row] = [] if user else queries.open_alerts(con)
        etag, not_modified = render.cached(request, user, limit, *queries.feed_signal(con, user, alerts))
        if not_modified:
            return not_modified
        entries: list[sqlite3.Row] = queries.posts(con, user, limit)
        extra_slides: dict[str, list[str]] = queries.extra_slides(con, [string(r, "id") for r in entries])
        avatars: dict[str, str] = queries.avatar_files(con)

    public_url: str = settings.PUBLIC_URL
    fg: FeedGenerator = render.new_feed(
        f"{public_url}/instagram.xml" + (f"?user={user}" if user else ""),
        f"Instagram — {user}" if user else "Instagram — Following",
    )

    # Each alert gets a new id per raise, so a reader shows it again if it's resolved and raised later.
    fe: FeedEntry
    alert: sqlite3.Row
    for alert in alerts:
        message: str = string(alert, "message")
        fe = fg.add_entry(order="append")
        fe.id(f"{public_url}/alert/{string(alert, 'kind')}/{string(alert, 'raised_at')}")
        fe.title(f"⚠ instadroid needs attention: {message[:90]}")
        fe.link(href=f"{public_url}/status")
        raised: datetime = parse_iso(text(alert, "raised_at")) or datetime.now(UTC)
        fe.updated(raised)
        fe.published(raised)
        fe.content(
            f"<p>{escape(message)}</p><p><small>since {render.utc(raised)} · "
            f'<a href="{public_url}/status">status page</a></small></p>',
            type="html",
        )

    username: str
    kind: str | None
    media_file: str | None
    posted_date: str | None
    place: str | None
    scraped_abs: datetime | None
    r: sqlite3.Row
    for r in entries:
        username, kind, media_file = string(r, "username"), text(r, "kind"), text(r, "media_file")
        fe = fg.add_entry(order="append")
        fe.id(f"{public_url}/post/{string(r, 'id')}")
        caption: str = text(r, "caption") or ""
        first_line: str = caption.split("\n", 1)[0][:90] or kind or "post"
        marker: str = "▶ " if kind == "video" else ""  # Reels and videos are stored as kind "video"
        fe.title(f"{marker}{username}: {first_line}")
        url: str = opt_str(r, "url") or f"https://www.instagram.com/{username}/"
        fe.link(href=url)
        fe.author(name=username)
        fe.updated(parse_iso(text(r, "scraped_at")) or datetime.now(UTC))
        posted_abs: datetime | None = parse_iso(opt_str(r, "posted_at"))
        if posted_abs:
            fe.published(posted_abs)
        html: str = ""
        avatar: str | None = avatars.get(username)
        if avatar:
            html += f'<p><img src="{render.media_url(avatar)}" alt="" width="48" height="48" /></p>'
        slide: str
        for slide in ([media_file] if media_file else []) + extra_slides.get(string(r, "id"), []):
            html += f"<p>{render.img(slide)}</p>"
        if media_file:
            render.thumbnail(fe, media_file)
        html += f"<p>{render.caption_html(caption)}</p>"
        # Both dates are also on the entry itself (<published>/<updated>) for readers that sort by
        # those, but spelling them out here means sorting-by-eye works in any reader.
        meta: list[str] = []
        if posted_date := text(r, "posted_date"):
            meta.append(
                f"Posted {escape(posted_date)}" + (f" ({render.utc(posted_abs)})" if posted_abs else "")
            )
        elif posted_abs:
            meta.append(f"Posted {render.utc(posted_abs)}")
        if place := opt_str(r, "place"):
            meta.append(f"at {escape(place)}")
        if scraped_abs := parse_iso(text(r, "scraped_at")):
            meta.append(f"saved {render.utc(scraped_abs)}")
        meta.append(f'<a href="{escape(url)}">open on Instagram</a>')
        html += f"<p><small>{' · '.join(meta)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


@router.get(
    "/stories.xml",
    response_class=Response,
    responses={200: {"content": {"application/atom+xml": {}}, "description": "An Atom feed."}, 304: {}},
    summary="Stories feed",
)
def stories_feed(request: Request, limit: int = 200) -> Response:
    """Stored story frames as Atom, newest first; `limit` is capped at 500."""
    con: sqlite3.Connection
    etag: str
    not_modified: Response | None
    with queries.connection() as con:
        etag, not_modified = render.cached(request, limit, *queries.stories_stats(con))
        if not_modified:
            return not_modified
        entries: list[sqlite3.Row] = queries.stories(con, limit)
    fg: FeedGenerator = render.new_feed(f"{settings.PUBLIC_URL}/stories.xml", "Instagram — Stories")

    username: str
    media_file: str | None
    fe: FeedEntry
    r: sqlite3.Row
    for r in entries:
        username, media_file = string(r, "username"), text(r, "media_file")
        fe = fg.add_entry(order="append")
        fe.id(f"{settings.PUBLIC_URL}/story/{string(r, 'id')}")
        fe.title(f"{username}'s story")
        fe.link(href=f"https://www.instagram.com/{username}/")
        fe.author(name=username)
        scraped: datetime = parse_iso(text(r, "scraped_at")) or datetime.now(UTC)
        fe.updated(scraped)
        fe.published(scraped)
        html: str = f"<p>{render.img(media_file)}</p>" if media_file else ""
        if media_file:
            render.thumbnail(fe, media_file)
        html += f"<p><small>saved {render.utc(scraped)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


@router.get("/users", summary="Accounts with stored posts")
def users() -> list[str]:
    """Every username with at least one stored post, sorted."""
    con: sqlite3.Connection
    with queries.connection() as con:
        return queries.usernames(con)


@router.get(
    "/opml",
    response_class=Response,
    responses={200: {"content": {"text/x-opml": {}}, "description": "An OPML outline."}, 304: {}},
    summary="Subscription list",
)
def opml(request: Request) -> Response:
    """One OPML outline nesting the aggregate feed, the stories feed, and one per-account feed
    per username in /users — a single FreshRSS import subscribes to everything this instance
    serves instead of pasting ?user= URLs in one at a time."""
    con: sqlite3.Connection
    with queries.connection() as con:
        usernames: list[str] = queries.usernames(con)
    etag: str
    not_modified: Response | None
    etag, not_modified = render.cached(request, settings.PUBLIC_URL, settings.FEED_TOKEN, *usernames)
    if not_modified:
        return not_modified

    root: Element = Element("opml", version="2.0")
    head: Element = SubElement(root, "head")
    SubElement(head, "title").text = "Instadroid"
    SubElement(head, "dateCreated").text = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    body: Element = SubElement(root, "body")
    category: Element = SubElement(body, "outline", text="Instagram", title="Instagram")

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
        category, "Instagram — Following", render.feed_url("/instagram.xml"), "https://www.instagram.com/"
    )
    _feed_outline(
        category, "Instagram — Stories", render.feed_url("/stories.xml"), "https://www.instagram.com/"
    )
    u: str
    for u in usernames:
        _feed_outline(
            category, u, render.feed_url("/instagram.xml", user=u), f"https://www.instagram.com/{quote(u)}/"
        )

    xml_bytes: bytes = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode()
    )
    return Response(xml_bytes, media_type="text/x-opml; charset=utf-8", headers={"ETag": etag})
