"""Account groups: GROUPS_FILE served as ?group= feeds, /opml categories and /subscribe's group section."""

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.feedclient import TWO_POSTS, make_app
from tests.support import Json, json_object

FRSS: str = "{https://freshrss.org/opml}"

THREE_POSTS: str = (
    TWO_POSTS
    + """
INSERT INTO posts VALUES ('h3', 'third', 'photo', '', 'Third', NULL,
    '2026-09-08T11:00:00+00:00', 'h3', NULL, NULL, '2026-09-06T09:00:00+00:00');
"""
)


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, groups: str | None) -> TestClient:
    """The feed server over THREE_POSTS (accounts someone, other, third) with `groups` as its groups
    file at the default path beside the database; None writes no file."""
    tmp_path.mkdir(exist_ok=True)
    if groups is not None:
        (tmp_path / "groups.toml").write_text(groups)
    monkeypatch.delenv("GROUPS_FILE", raising=False)
    return make_app(tmp_path, monkeypatch, seed=THREE_POSTS)


def _categories(r: Response) -> list[tuple[str | None, str | None, list[str | None]]]:
    """(name, Dynamic OPML URL, feed URLs) of each category of an /opml answer, in order."""
    return [
        (c.get("text"), c.get(f"{FRSS}opmlUrl"), [o.get("xmlUrl") for o in c.findall("outline")])
        for c in ET.fromstring(r.text).findall("./body/outline")
    ]


def _authors(r: Response) -> list[str | None]:
    return [
        e.findtext("{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name")
        for e in ET.fromstring(r.text).findall("{http://www.w3.org/2005/Atom}entry")
    ]


def test_group_feed_holds_its_members_posts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = _app(tmp_path, monkeypatch, 'pals = ["@Someone", "third", "nobody"]\n')
    r: Response = client.get("/instagram.xml", params={"group": "pals"})
    assert r.status_code == 200
    assert "<title>Instagram — pals</title>" in r.text
    assert "Hi &lt;there&gt;" in r.text and "Third" in r.text
    assert "instagram.xml?group=pals" in r.text  # the feed's link to itself keeps the group
    assert sorted(_authors(r), key=str) == ["someone", "third"]

    as_json: Response = client.get("/instagram.json", params={"group": "pals", "kind": "photo"})
    assert as_json.status_code == 200
    items: Json = json_object(as_json)["items"]
    assert isinstance(items, list) and len(items) == 2


def test_group_feed_and_user_narrow_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = _app(tmp_path, monkeypatch, 'pals = ["someone"]\n')
    assert "<entry>" not in client.get("/instagram.xml", params={"group": "pals", "user": "other"}).text


def test_unknown_group_is_a_404_naming_the_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = _app(tmp_path, monkeypatch, 'pals = ["someone"]\nwork = []\n')
    r: Response = client.get("/instagram.xml", params={"group": "nope"})
    assert r.status_code == 404
    assert "pals, work" in r.json()["detail"]
    assert _app(tmp_path / "none", monkeypatch, None).get("/opml", params={"group": "x"}).status_code == 404


def test_empty_group_is_an_empty_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r: Response = _app(tmp_path, monkeypatch, "work = []\n").get("/instagram.xml", params={"group": "work"})
    assert r.status_code == 200
    assert "<entry>" not in r.text


def test_group_feed_etag_moves_when_the_group_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = _app(tmp_path, monkeypatch, 'pals = ["someone"]\n')
    etag: str = client.get("/instagram.xml", params={"group": "pals"}).headers["etag"]
    (tmp_path / "groups.toml").write_text('pals = ["someone", "other"]\n')  # read again: no restart
    r: Response = client.get("/instagram.xml", params={"group": "pals"}, headers={"if-none-match": etag})
    assert r.status_code == 200
    assert sorted(_authors(r), key=str) == ["other", "someone"]


def test_opml_without_a_groups_file_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r: Response = _app(tmp_path, monkeypatch, None).get("/opml")
    assert _categories(r) == [
        (
            "Instagram",
            "http://feed.test/opml",
            [
                "http://feed.test/stories.xml",
                "http://feed.test/instagram.xml?user=other",
                "http://feed.test/instagram.xml?user=someone",
                "http://feed.test/instagram.xml?user=third",
            ],
        )
    ]


def test_opml_has_a_category_per_group_then_the_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # "third" is in both groups: /opml lists it under the first. "empty" lists no stored account.
    client: TestClient = _app(
        tmp_path, monkeypatch, 'b = ["third", "someone"]\nempty = ["nobody"]\n"a & c" = ["third"]\n'
    )
    r: Response = client.get("/opml", params={"aggregate": "1"})
    assert r.status_code == 200
    assert _categories(r) == [
        (
            "b",
            "http://feed.test/opml?group=b",
            ["http://feed.test/instagram.xml?user=someone", "http://feed.test/instagram.xml?user=third"],
        ),
        (
            "Instagram",
            "http://feed.test/opml?ungrouped=1&aggregate=1",
            [
                "http://feed.test/instagram.xml",
                "http://feed.test/stories.xml",
                "http://feed.test/instagram.xml?user=other",
            ],
        ),
    ]
    # Each category's own list is that category alone, so FreshRSS keeps each in step separately.
    assert _categories(client.get("/opml", params={"group": "b"})) == [_categories(r)[0]]
    assert _categories(client.get("/opml", params={"ungrouped": "1", "aggregate": "1"})) == [
        _categories(r)[1]
    ]
    assert _categories(client.get("/opml", params={"group": "a & c"})) == [
        ("a & c", "http://feed.test/opml?group=a%20%26%20c", [])
    ]


def test_opml_etag_moves_when_the_groups_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = _app(tmp_path, monkeypatch, 'pals = ["someone"]\n')
    etag: str = client.get("/opml").headers["etag"]
    (tmp_path / "groups.toml").write_text('pals = ["someone"]\nmore = ["other"]\n')
    r: Response = client.get("/opml", headers={"if-none-match": etag})
    assert r.status_code == 200
    assert [c[0] for c in _categories(r)] == ["pals", "more", "Instagram"]


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("pals = [", "not TOML"),
        ('pals = "someone"\n', "not a list of usernames"),
        ("pals = [1]\n", "not a list of usernames"),
        ('pals = ["@"]\n', "empty username"),
        ('"" = ["someone"]\n', "empty name"),
    ],
)
def test_broken_groups_file_is_a_503_on_group_routes_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    text: str,
    problem: str,
) -> None:
    """A Dynamic OPML re-read that gets a 503 keeps its category as it was instead of muting feeds."""
    client: TestClient = _app(tmp_path, monkeypatch, text)
    r: Response = client.get("/opml")
    assert r.status_code == 503
    assert r.headers["retry-after"] == "60"
    assert problem in r.json()["detail"]
    assert client.get("/instagram.xml", params={"group": "pals"}).status_code == 503
    assert client.get("/instagram.xml").status_code == 200
    page: Response = client.get("/subscribe")
    assert page.status_code == 200
    assert f"<code>{tmp_path / 'groups.toml'}</code> is unreadable or not a list of groups" in page.text
    assert problem not in page.text  # the error's own text goes to the log only
    assert problem in caplog.text


def _not_utf8(path: Path) -> None:
    _ = path.write_bytes(b'pals = ["\xff"]\n')


def _a_directory(path: Path) -> None:
    path.mkdir()


@pytest.mark.parametrize(
    ("make", "problem"),
    [
        (_not_utf8, "unreadable: 'utf-8' codec can't decode"),
        (_a_directory, "unreadable: [Errno 21] Is a directory"),
    ],
)
def test_groups_file_that_can_t_be_read_is_a_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make: Callable[[Path], None], problem: str
) -> None:
    client: TestClient = _app(tmp_path, monkeypatch, None)
    make(tmp_path / "groups.toml")
    r: Response = client.get("/opml")
    assert r.status_code == 503 and problem in r.json()["detail"]


def test_groups_file_under_a_file_is_a_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "not-a-dir").write_text("")
    monkeypatch.setenv("GROUPS_FILE", str(tmp_path / "not-a-dir" / "groups.toml"))
    client: TestClient = make_app(tmp_path, monkeypatch, seed=THREE_POSTS)
    r: Response = client.get("/opml")
    assert r.status_code == 503 and "unreadable: [Errno 20] Not a directory" in r.json()["detail"]


def test_groups_file_setting_overrides_the_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere: Path = tmp_path / "conf" / "mine.toml"
    elsewhere.parent.mkdir()
    elsewhere.write_text('pals = ["other"]\n')
    monkeypatch.setenv("GROUPS_FILE", str(elsewhere))
    client: TestClient = make_app(tmp_path, monkeypatch, seed=THREE_POSTS)
    assert _authors(client.get("/instagram.xml", params={"group": "pals"})) == ["other"]


def test_subscribe_lists_each_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page: str = _app(tmp_path, monkeypatch, 'pals = ["someone"]\n').get("/subscribe").text
    assert "<h2>By group</h2>" in page
    assert "http://feed.test/instagram.xml?group=pals" in page
    assert "http://feed.test/opml?group=pals" in page
    assert "http://feed.test/opml?ungrouped=1" in page
    assert "By group" not in _app(tmp_path / "none", monkeypatch, None).get("/subscribe").text
