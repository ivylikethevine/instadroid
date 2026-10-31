"""The subscription routes: /instagram.xml and /stories.xml, the same as JSON Feed (/instagram.json,
/stories.json), /users and /opml."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import Annotated
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import APIRouter, HTTPException, Query, Request, Response
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator

from . import queries, render, settings
from .entries import Entry, alert_entries, post_entries, story_entries

router: APIRouter = APIRouter()


KINDS: dict[str, str] = {
    "photo": "photo",
    "carousel": "carousel",
    "reel": "video",
    "video": "video",
    "post": "post",
}
JSON_FEED: str = "application/feed+json"

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]


def _kinds(kind: str | None) -> tuple[str, ...]:
    """?kind=photo,reel as the stored kinds it selects; 422 for a name not in KINDS."""
    names: list[str] = [k.strip().lower() for k in (kind or "").split(",") if k.strip()]
    unknown: list[str] = [k for k in names if k not in KINDS]
    if unknown:
        raise HTTPException(422, f"unknown kind {', '.join(unknown)} (one of: {', '.join(KINDS)})")
    return tuple(sorted({KINDS[k] for k in names}))


def _since(since: str | None) -> str | None:
    """?since= as a UTC ISO timestamp. A date is its midnight, and a time without an offset is read, in
    the TZ setting's zone; 422 when it's neither a date nor a time."""
    if not since:
        return None
    parsed: datetime
    try:
        parsed = datetime.fromisoformat(since.strip())
    except ValueError:
        raise HTTPException(422, f"since={since!r} is not an ISO date or time") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=settings.ZONE)
    return parsed.astimezone(UTC).isoformat()


def _atom(feed_url: str, title: str, entries: list[Entry], etag: str) -> Response:
    fg: FeedGenerator = render.new_feed(feed_url, title)
    fe: FeedEntry
    e: Entry
    for e in entries:
        fe = fg.add_entry(order="append")
        fe.id(e.id)
        fe.title(e.title)
        fe.link(href=e.url)
        if e.author:
            fe.author(name=e.author)
        fe.updated(e.updated)
        if e.published:
            fe.published(e.published)
        if e.image:
            render.thumbnail(fe, e.image)
        fe.content(e.html, type="html")
    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


def _json_item(e: Entry) -> dict[str, Json]:
    item: dict[str, Json] = {
        "id": e.id,
        "url": e.url,
        "title": e.title,
        "content_html": e.html,
        "date_modified": e.updated.isoformat(),
    }
    if e.published:
        item["date_published"] = e.published.isoformat()
    if e.image:
        item["image"] = render.media_url(e.image)
    if e.author:
        author: dict[str, Json] = {"name": e.author, "url": e.author_url}
        if e.avatar:
            author["avatar"] = render.media_url(e.avatar)
        item["authors"] = [author]
    return item


def _json(feed_url: str, title: str, home: str, entries: list[Entry], etag: str) -> Response:
    """A JSON Feed 1.1 document (https://www.jsonfeed.org/version/1.1/)."""
    body: dict[str, Json] = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": title,
        "home_page_url": home,
        "feed_url": feed_url,
        "items": [_json_item(e) for e in entries],
    }
    return Response(json.dumps(body, ensure_ascii=False), media_type=JSON_FEED, headers={"ETag": etag})


def _respond(fmt: str, feed_url: str, title: str, home: str, entries: list[Entry], etag: str) -> Response:
    """`entries` as JSON Feed for "json", else as Atom."""
    if fmt == "json":
        return _json(feed_url, title, home, entries, etag)
    return _atom(feed_url, title, entries, etag)


def _posts_feed(
    request: Request,
    fmt: str,
    user: str | None,
    kind: str | None,
    since: str | None,
    q: str | None,
    limit: int,
) -> Response:
    where: queries.PostFilter = queries.PostFilter(user, _kinds(kind), _since(since), q or None)
    con: sqlite3.Connection
    etag: str
    not_modified: Response | None
    with queries.connection() as con:
        # Open failure alerts go first in the aggregate feed, where a reader is already looking.
        alerts: list[sqlite3.Row] = [] if user else queries.open_alerts(con)
        etag, not_modified = render.cached(
            request,
            fmt,
            user,
            where.kinds,
            where.since,
            where.q,
            limit,
            *queries.feed_signal(con, user, alerts),
        )
        if not_modified:
            return not_modified
        entries: list[Entry] = alert_entries(alerts) + post_entries(con, queries.posts(con, where, limit))
    feed_url: str = render.feed_url(f"/instagram.{fmt}", token=False, user=user, kind=kind, since=since, q=q)
    title: str = f"Instagram — {user}" if user else "Instagram — Following"
    home: str = render.profile_url(user) if user else "https://www.instagram.com/"
    return _respond(fmt, feed_url, title, home, entries, etag)


def _stories_feed(request: Request, fmt: str, limit: int) -> Response:
    con: sqlite3.Connection
    etag: str
    not_modified: Response | None
    with queries.connection() as con:
        etag, not_modified = render.cached(request, fmt, limit, *queries.stories_stats(con))
        if not_modified:
            return not_modified
        entries: list[Entry] = story_entries(queries.stories(con, limit))
    feed_url: str = f"{settings.PUBLIC_URL}/stories.{fmt}"
    return _respond(fmt, feed_url, "Instagram — Stories", "https://www.instagram.com/", entries, etag)


_KIND_HELP: str = "Comma-separated: photo, carousel, reel (or video), post."
_SINCE_HELP: str = "Posts from this ISO date or time on; without an offset, in the TZ setting's zone."
_Q_HELP: str = "Posts whose caption contains this text (case-insensitive for ASCII)."


# responses= for each feed route, written out on each: FastAPI types that parameter as
# dict[int | str, dict[str, Any]], which only a literal in place matches without an Any of our own.
@router.get(
    "/instagram.xml",
    response_class=Response,
    responses={200: {"content": {"application/atom+xml": {}}, "description": "An Atom feed."}, 304: {}},
    summary="Posts feed",
)
def feed(
    request: Request,
    user: str | None = None,
    kind: Annotated[str | None, Query(description=_KIND_HELP)] = None,
    since: Annotated[str | None, Query(description=_SINCE_HELP)] = None,
    q: Annotated[str | None, Query(description=_Q_HELP)] = None,
    limit: int = 200,
) -> Response:
    """Stored posts as Atom, newest first. `user` narrows it to one account, and `kind`, `since` and
    `q` narrow it further; `limit` is capped at 500."""
    return _posts_feed(request, "xml", user, kind, since, q, limit)


@router.get(
    "/instagram.json",
    response_class=Response,
    responses={
        200: {"content": {"application/feed+json": {}}, "description": "A JSON Feed 1.1 document."},
        304: {},
    },
    summary="Posts feed, as JSON Feed",
)
def json_feed(
    request: Request,
    user: str | None = None,
    kind: Annotated[str | None, Query(description=_KIND_HELP)] = None,
    since: Annotated[str | None, Query(description=_SINCE_HELP)] = None,
    q: Annotated[str | None, Query(description=_Q_HELP)] = None,
    limit: int = 200,
) -> Response:
    """/instagram.xml's entries, with the same parameters, as JSON Feed 1.1."""
    return _posts_feed(request, "json", user, kind, since, q, limit)


@router.get(
    "/stories.xml",
    response_class=Response,
    responses={200: {"content": {"application/atom+xml": {}}, "description": "An Atom feed."}, 304: {}},
    summary="Stories feed",
)
def stories_feed(request: Request, limit: int = 200) -> Response:
    """Stored story frames as Atom, newest first; `limit` is capped at 500."""
    return _stories_feed(request, "xml", limit)


@router.get(
    "/stories.json",
    response_class=Response,
    responses={
        200: {"content": {"application/feed+json": {}}, "description": "A JSON Feed 1.1 document."},
        304: {},
    },
    summary="Stories feed, as JSON Feed",
)
def stories_json_feed(request: Request, limit: int = 200) -> Response:
    """/stories.xml's entries as JSON Feed 1.1."""
    return _stories_feed(request, "json", limit)


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
def opml(request: Request, aggregate: bool = True) -> Response:
    """One OPML outline nesting the aggregate feed, the stories feed, and one per-account feed
    per username in /users — a single FreshRSS import subscribes to everything this instance
    serves instead of pasting ?user= URLs in one at a time. `aggregate=0` leaves the aggregate feed
    out: a reader dedupes entries within a feed, so beside the per-account feeds it shows every post twice."""
    con: sqlite3.Connection
    with queries.connection() as con:
        usernames: list[str] = queries.usernames(con)
    etag: str
    not_modified: Response | None
    etag, not_modified = render.cached(
        request, settings.PUBLIC_URL, settings.FEED_TOKEN, aggregate, *usernames
    )
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

    if aggregate:
        _feed_outline(
            category, "Instagram — Following", render.feed_url("/instagram.xml"), "https://www.instagram.com/"
        )
    _feed_outline(
        category, "Instagram — Stories", render.feed_url("/stories.xml"), "https://www.instagram.com/"
    )
    u: str
    for u in usernames:
        _feed_outline(category, u, render.feed_url("/instagram.xml", user=u), render.profile_url(u))

    xml_bytes: bytes = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode()
    )
    return Response(xml_bytes, media_type="text/x-opml; charset=utf-8", headers={"ETag": etag})
