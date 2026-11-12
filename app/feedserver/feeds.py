"""The subscription routes: /instagram.xml and /stories.xml, the same as JSON Feed (/instagram.json,
/stories.json), /users and /opml."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import Annotated
from xml.etree.ElementTree import Element, SubElement, register_namespace, tostring

from fastapi import APIRouter, HTTPException, Query, Request, Response
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator
from shared.captions import profile_url
from shared.jsonvalues import JSON

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
INSTAGRAM_HOME: str = "https://www.instagram.com/"
# FreshRSS's OPML extension namespace, which its importer reads per-feed settings (frss:ttl, the
# refresh interval) and a category's Dynamic OPML URL (frss:opmlUrl) from. The prefix has to be
# literally "frss": the importer matches the qualified name.
FRSS_NS: str = "https://freshrss.org/opml"
register_namespace("frss", FRSS_NS)
# Seconds, sent with /opml's 503. FreshRSS stops fetching a URL that answered 503 (on a public
# hostname, every URL on that host and port) for the Retry-After it was given, and for 25 minutes by
# default when it was given none.
NOT_READY_RETRY_AFTER: str = "60"


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


def _json_item(e: Entry) -> dict[str, JSON]:
    item: dict[str, JSON] = {
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
        author: dict[str, JSON] = {"name": e.author, "url": e.author_url}
        if e.avatar:
            author["avatar"] = render.media_url(e.avatar)
        item["authors"] = [author]
    return item


def _json(feed_url: str, title: str, home: str, entries: list[Entry], etag: str) -> Response:
    """A JSON Feed 1.1 document (https://www.jsonfeed.org/version/1.1/)."""
    body: dict[str, JSON] = {
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
    feed_url: str = render.feed_url(
        settings.PUBLIC_URL, f"/instagram.{fmt}", token=False, user=user, kind=kind, since=since, q=q
    )
    title: str = f"Instagram — {user}" if user else "Instagram — Following"
    home: str = profile_url(user) if user else INSTAGRAM_HOME
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
    return _respond(fmt, feed_url, "Instagram — Stories", INSTAGRAM_HOME, entries, etag)


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


def feed_outline(text: str, xml_url: str, html_url: str) -> Element:
    """One feed of a subscription list, with the FreshRSS refresh interval when OPML_FEED_TTL sets one."""
    outline: Element = Element("outline", type="rss", text=text, title=text, xmlUrl=xml_url, htmlUrl=html_url)
    if settings.OPML_FEED_TTL > 0:
        outline.set(f"{{{FRSS_NS}}}ttl", str(settings.OPML_FEED_TTL))
    return outline


def category_outline(base: str, aggregate: bool = False) -> Element:
    """The "Instagram" category a subscription list nests its feeds under. Its `frss:opmlUrl` is the
    list's own URL: FreshRSS makes a category imported from a file with it a Dynamic OPML one, which it
    then keeps in step with /opml, and ignores it in an outline it fetched itself."""
    category: Element = Element("outline", text="Instagram", title="Instagram")
    own_url: str = render.feed_url(base, "/opml", aggregate="1" if aggregate else None)
    category.set(f"{{{FRSS_NS}}}opmlUrl", own_url)
    return category


@router.get(
    "/opml",
    response_class=Response,
    responses={
        200: {"content": {"text/x-opml": {}}, "description": "An OPML outline."},
        304: {},
        503: {
            "description": "The scraper hasn't created the database yet; the outline would be empty."
            " Carries `Retry-After`."
        },
    },
    summary="Subscription list",
)
def opml(request: Request, aggregate: bool = False) -> Response:
    """One OPML outline nesting the stories feed and one per-account feed per username in /users — a
    single FreshRSS import subscribes to every account instead of pasting ?user= URLs in one at a time.
    `aggregate=1` adds the aggregate feed too; a reader dedupes entries within a feed, so beside the
    per-account feeds it shows every post twice. The feed URLs start with PUBLIC_URL when that is set,
    else with the address this request came in on. The category names this URL as its FreshRSS Dynamic
    OPML URL, so a FreshRSS that imports the saved file keeps following it.
    Before the scraper's first start there is no posts table, and the answer is a 503 rather than an
    outline without accounts: a reader re-reading it as a Dynamic OPML would mute every account feed."""
    con: sqlite3.Connection
    usernames: list[str]
    with queries.connection() as con:
        if not queries.has_posts_table(con):
            raise HTTPException(
                503,
                "the scraper hasn't created the database yet; try again after its first run",
                headers={"Retry-After": NOT_READY_RETRY_AFTER},
            )
        usernames = queries.usernames(con)
    base: str = render.feed_base(request)
    etag: str
    not_modified: Response | None
    etag, not_modified = render.cached(
        request, base, settings.FEED_TOKEN, settings.OPML_FEED_TTL, aggregate, *usernames
    )
    if not_modified:
        return not_modified

    root: Element = Element("opml", version="2.0")
    head: Element = SubElement(root, "head")
    SubElement(head, "title").text = "Instadroid"
    SubElement(head, "dateCreated").text = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    body: Element = SubElement(root, "body")
    category: Element = category_outline(base, aggregate)
    body.append(category)
    if aggregate:
        category.append(
            feed_outline("Instagram — Following", render.feed_url(base, "/instagram.xml"), INSTAGRAM_HOME)
        )
    category.append(
        feed_outline("Instagram — Stories", render.feed_url(base, "/stories.xml"), INSTAGRAM_HOME)
    )
    u: str
    for u in usernames:
        category.append(feed_outline(u, render.feed_url(base, "/instagram.xml", user=u), profile_url(u)))

    xml_bytes: bytes = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode()
    )
    # The filename is for "save, then import the file": FreshRSS guesses a file's type from its name,
    # so a browser's default name for this path ("opml", no extension) still imports, but a renamed
    # download does too as long as it keeps the extension.
    headers: dict[str, str] = {"ETag": etag, "Content-Disposition": 'inline; filename="instadroid.opml"'}
    return Response(xml_bytes, media_type="text/x-opml; charset=utf-8", headers=headers)
