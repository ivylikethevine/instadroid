"""One post, one row: a card recognised under its collapsed and expanded caption, without a date, and
through a Copy link that repeats the last shortcode."""

import sqlite3

import pytest
from instadroid import alerts, config, db, parsing, scrape
from shared import sqlrows
from shared.sqlrows import SqlValue

from tests.deviceflows import (
    CAPTION,
    TOP_URL,
    copy_link,
    feed_cards,
    feed_device,
    following_screen,
    seed_post,
    top_card_id,
)
from tests.fakedevice import FakeDevice, Node, node
from tests.support import fetch_row, row_dict

pytestmark: pytest.MarkDecorator = pytest.mark.usefixtures("fast_offline")

LONG: str = "A caption long enough to be cut short in the feed, with more words after the cut"
COLLAPSED: str = "someone_nice A caption long enough… more"
PERMALINK: str = "https://www.instagram.com/reel/TOP123/"


@pytest.fixture(autouse=True)
def one_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run over the first Following screen only: no stories, no scroll."""
    monkeypatch.setattr(config, "MAX_STORIES_PER_RUN", 0)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)


def _card(text: str, *, header: bool = True, goto: str | None = None, share: str = "share_top") -> list[Node]:
    """A photo card by someone_nice with this caption text, its header on screen or scrolled off."""
    head: list[Node] = [
        node(
            "row_feed_profile_header",
            desc="someone_nice posted a photo 3 hours ago",
            bounds=(0, 300, 1080, 437),
        )
    ]
    return (head if header else []) + [
        node("row_feed_photo_imageview", desc="Photo by Someone Nice, 5 likes", bounds=(0, 437, 1080, 1500)),
        node("row_feed_button_share", bounds=(390, 1500, 453, 1621), goto=share),
        node(cls=CAPTION, text=text, bounds=(32, 1630, 1080, 1700), goto=goto),
    ]


def _device(cards: list[Node], expanded: list[Node] | None = None) -> FakeDevice:
    """The Following feed showing `cards`; tapping a caption's "more" shows `expanded` instead."""
    d: FakeDevice = feed_device()
    d.screens["following"] = following_screen(cards)
    d.screens["expanded"] = following_screen(expanded if expanded is not None else cards)
    d.screens["share_expanded"] = following_screen(sheet=copy_link(TOP_URL))
    d.back |= {"expanded": "home", "share_expanded": "expanded"}
    d.scroll = {}
    return d


def _hash(cards: list[Node]) -> str:
    """parsing.post_id() of the first of `cards` as the Following feed shows it."""
    return parsing.post_id(parsing.parse_hierarchy(following_screen(cards))[0])


def _post(con: sqlite3.Connection, post_id: str = "TOP123") -> dict[str, SqlValue]:
    return row_dict(fetch_row(con.execute("SELECT * FROM posts WHERE id=?", (post_id,))))


def _expanding_device() -> FakeDevice:
    return _device(_card(COLLAPSED, goto="expanded"), _card(f"someone_nice {LONG}", share="share_expanded"))


def test_a_card_is_not_captured_again_once_its_caption_is_expanded() -> None:
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = _expanding_device()
    collapsed: str = _hash(_card(COLLAPSED))

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert stats["new"] == 1 and stats["metrics"].get("link_clipboard_failures") == 0
    assert "share_expanded" not in d.history  # the expanded card was never taken for a new one
    stored: dict[str, SqlValue] = _post(con)
    assert stored["caption"] == LONG and stored["hash"] == collapsed
    assert stored["alt_hash"] not in (None, collapsed)
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts")) == 1


def test_a_later_run_recognises_the_post_under_either_caption_form() -> None:
    con: sqlite3.Connection = db.db_init()
    scrape.scrape_once(_expanding_device(), con)
    cards: list[Node]
    for cards in (_card(COLLAPSED), _card(f"someone_nice {LONG}")):
        d: FakeDevice = _device(cards)
        assert scrape.scrape_once(d, con)["new"] == 0
        assert "share_top" not in d.history  # recognised from the hash alone
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts")) == 1


def test_a_card_with_no_date_is_matched_to_its_stored_post_by_caption() -> None:
    con: sqlite3.Connection = db.db_init()
    seed_post(con, "TOP123", "someone_nice", LONG, 0, h="stored-key", url=PERMALINK)
    d: FakeDevice = _device(_card(f"someone_nice {LONG}", header=False))

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert stats["new"] == 0 and "share_top" not in d.history  # no crop, no share sheet
    stored: dict[str, SqlValue] = _post(con)
    assert (stored["hash"], stored["alt_hash"]) == (_hash(_card(f"someone_nice {LONG}")), "stored-key")
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts")) == 1


def test_a_header_less_card_with_no_date_and_no_permalink_is_not_stored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "PERMALINK_RETRIES", 0)
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = _device(_card(f"someone_nice {LONG}", header=False, share="share_noclip"))

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert stats["new"] == 0 and stats["metrics"].get("link_clipboard_failures") == 1
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts")) == 0
    assert not list(config.MEDIA_DIR.glob("*.webp"))  # its crop went with it
    assert "header-less card: no date and no permalink" in capsys.readouterr().out


def test_the_same_shortcode_twice_is_the_same_post_copied_again() -> None:
    """The clipboard still holds the link the last run ended on, and this run's first card is that
    post under a hash nobody stored: Copy link "repeats", and the stored row says why."""
    con: sqlite3.Connection = db.db_init()
    seed_post(
        con, "TOP123", "someone_nice", "Top card caption, and the rest of it", 1, h="stale", url=PERMALINK
    )
    d: FakeDevice = feed_device()
    d.clipboard, d.clipboard_settable = TOP_URL, False

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert stats["metrics"].get("link_clipboard_failures") == 0
    stored: dict[str, SqlValue] = _post(con)
    assert (stored["hash"], stored["alt_hash"]) == (top_card_id(feed_device(start="following")), "stale")
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts WHERE username='someone_nice'")) == 1


def test_a_repeated_shortcode_for_a_post_that_is_not_stored_is_a_clipboard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "PERMALINK_RETRIES", 0)
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    con: sqlite3.Connection = db.db_init()
    d: FakeDevice = feed_device()
    d.clipboard, d.clipboard_settable = TOP_URL, False

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert stats["metrics"].get("link_clipboard_failures") == 1
    stored: sqlite3.Row = fetch_row(
        con.execute("SELECT id, url, hash FROM posts WHERE username='someone_nice'")
    )
    assert stored["url"] is None and stored["id"] == stored["hash"]


def test_a_repeated_shortcode_stored_for_another_caption_is_a_clipboard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "PERMALINK_RETRIES", 0)
    con: sqlite3.Connection = db.db_init()
    seed_post(con, "TOP123", "someone_nice", "Something else entirely", 9, h="stale", url=PERMALINK)
    d: FakeDevice = feed_device()
    d.clipboard, d.clipboard_settable = TOP_URL, False

    assert scrape.scrape_once(d, con)["metrics"].get("link_clipboard_failures") == 1
    assert _post(con)["hash"] == "stale"


def test_same_caption_accepts_a_collapsed_start_and_a_placeholder() -> None:
    assert parsing.flat_caption("First line\n\nsecond  line…") == "First line second line"
    assert parsing.same_caption("First line\nsecond line", "First line sec…")
    assert parsing.same_caption("First li…", "First line\nsecond line")
    assert parsing.same_caption("Photo by Someone Nice, 5 likes", "Anything at all")
    assert not parsing.same_caption("First line", "Another line")


def test_a_stored_post_back_under_another_username_is_a_suspected_rename() -> None:
    con: sqlite3.Connection = db.db_init()
    seed_post(con, "TOP123", "former_name", "Top card caption, and the rest of it", 1, h="old", url=PERMALINK)
    d: FakeDevice = feed_device()

    stats: scrape.RunStats = scrape.scrape_once(d, con)

    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts WHERE username='someone_nice'")) == 0
    assert db.rename_candidates(con) == [("former_name", "someone_nice", 1)]
    assert "former_name now posts as someone_nice?" in str(stats["metrics"].get("warning"))
    assert "`scraper.py rename former_name someone_nice`" in alerts.conditions(con)[alerts.RENAME]

    assert scrape.scrape_once(feed_device(), con)["new"] == 0  # recognised by its hash from now on
    assert db.rename_account(con, "former_name", "someone_nice") == 1
    assert db.rename_candidates(con) == [] and alerts.RENAME not in alerts.conditions(con)


def test_a_post_two_accounts_share_is_not_a_rename() -> None:
    con: sqlite3.Connection = db.db_init()
    seed_post(con, "TOP123", "former_name", "Top card caption, and the rest of it", 1, h="old", url=PERMALINK)
    d: FakeDevice = feed_device()
    shared: Node = node(
        cls="android.widget.Button", text="former_name and someone_nice", bounds=(0, 289, 1080, 290)
    )
    d.screens["following"] = following_screen([shared, *feed_cards()])

    scrape.scrape_once(d, con)

    assert db.rename_candidates(con) == []
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts WHERE username='someone_nice'")) == 0


def _suspect(con: sqlite3.Connection) -> None:
    """One run that sees stored post TOP123, former_name's, under someone_nice."""
    seed_post(con, "TOP123", "former_name", "Top card caption, and the rest of it", 1, h="old", url=PERMALINK)
    scrape.scrape_once(feed_device(), con)
    assert db.rename_candidates(con) == [("former_name", "someone_nice", 1)]


def _follow(con: sqlite3.Connection, *usernames: str) -> None:
    con.execute("DELETE FROM following")
    con.executemany(
        "INSERT INTO following VALUES (?, '2026-09-14T00:00:00+00:00')", [(u,) for u in usernames]
    )
    con.commit()


def test_the_following_list_confirms_a_rename_only_with_the_new_name_and_not_the_old() -> None:
    con: sqlite3.Connection = db.db_init()
    _suspect(con)
    assert db.confirmed_renames(con) == []  # no stored list
    _follow(con, "former_name", "someone_nice")
    assert db.confirmed_renames(con) == []  # the old name is still followed: two accounts
    _follow(con, "other_user")
    assert db.confirmed_renames(con) == []  # the new name isn't followed
    _follow(con, "someone_nice", "other_user")
    assert db.confirmed_renames(con) == [("former_name", "someone_nice")]


def test_a_rename_with_more_than_one_reading_is_left_for_a_person() -> None:
    con: sqlite3.Connection = db.db_init()
    _suspect(con)
    _follow(con, "someone_nice", "other_user")
    seed_post(con, "OTHER9", "former_name", "Another caption", 2, h="other9")
    db.note_rename_candidate(con, "former_name", "other_user", "OTHER9")
    assert db.confirmed_renames(con) == []  # one old name, two new ones
    db.dismiss_rename(con, "former_name", "other_user")
    seed_post(con, "THIRD9", "third_name", "A third caption", 3, h="third9")
    db.note_rename_candidate(con, "third_name", "someone_nice", "THIRD9")
    assert db.confirmed_renames(con) == []  # two old names, one new one


def test_a_confirmed_rename_is_applied_only_when_that_is_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    con: sqlite3.Connection = db.db_init()
    _suspect(con)
    _follow(con, "someone_nice", "other_user")
    monkeypatch.setattr(config, "FOLLOWING_REFRESH_DAYS", 0)  # the stored list is read, never refreshed here

    assert not config.RENAME_AUTO_APPLY
    scrape.scrape_once(feed_device(), con)
    assert db.rename_candidates(con) == [("former_name", "someone_nice", 1)]

    monkeypatch.setattr(config, "RENAME_AUTO_APPLY", True)
    stats: scrape.RunStats = scrape.scrape_once(feed_device(), con)
    assert "renamed former_name to someone_nice: 1 post(s) moved" in str(stats["metrics"].get("warning"))
    assert sqlrows.scalar(con.execute("SELECT COUNT(*) FROM posts WHERE username='former_name'")) == 0
    assert db.rename_candidates(con) == [] and alerts.RENAME not in alerts.conditions(con)


def test_a_dismissed_rename_is_not_suspected_again() -> None:
    con: sqlite3.Connection = db.db_init()
    _suspect(con)
    assert db.dismiss_rename(con, "former_name", "someone_nice") == 1
    assert db.rename_candidates(con) == [] and alerts.RENAME not in alerts.conditions(con)
    con.execute(
        "UPDATE posts SET hash='older', alt_hash=NULL WHERE id='TOP123'"
    )  # so the card isn't known by hash
    con.commit()
    scrape.scrape_once(feed_device(), con)
    assert db.rename_candidates(con) == []
