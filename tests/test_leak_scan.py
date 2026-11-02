"""leak-scan (devtools/leak_scan.py): collecting identifiers from a deployment's data, and finding them,
or leak-shaped text, in a repository's tree and history. Every repository and data directory here is a
throwaway one under tmp_path."""

import os
import re
import sqlite3
import subprocess
from collections.abc import Iterator
from pathlib import Path

import igprofiles
import pytest
from devtools import leak_scan
from devtools.leak_scan import Finding, Scanner

ALLOWED: frozenset[str] = leak_scan.allowlist()


def _database(path: Path) -> None:
    """A scraper database with a few of the identifying columns, and a table lacking the one looked for."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con: sqlite3.Connection = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE posts (id TEXT, username TEXT, caption TEXT, place TEXT, url TEXT);
        CREATE TABLE following (username TEXT);
        CREATE TABLE stories (id TEXT);
        INSERT INTO posts VALUES ('Cq1Ab2Cd3Ef', 'real.poster', 'A long first line of a real caption
short', 'Real Cafe', 'https://www.instagram.com/p/Zz9Yy8Xx7Ww/');
        INSERT INTO posts VALUES ('0123456789abcdef', 'user7', NULL, NULL, NULL);
        INSERT INTO following VALUES ('gone_since');
        """
    )
    con.commit()
    con.close()


def _data_dir(root: Path) -> Path:
    data: Path = root / "data"
    _database(data / "db" / "backups" / "posts-2026-10-01.sqlite")
    _database(data / "android" / "data" / "instagram.db")  # someone else's database: skipped
    media: Path = data / "media"
    (media / "avatars").mkdir(parents=True)
    (media / "stories").mkdir()
    (media / "avatars" / "avatar_owner.png").write_bytes(b"png")
    (media / "Qw1Er2Ty3Ui_1.jpg").write_bytes(b"jpg")
    (media / "stories" / "Hh1Hh2Hh3Hh.jpg").write_bytes(b"jpg")  # a story crop says nothing
    (media / "0123abcd4567.jpg").write_bytes(b"jpg")  # a content hash, not a shortcode
    (data / "notes.txt").write_text("not data")
    debug: Path = data / "debug"
    debug.mkdir()
    following: str = igprofiles.fixture("v424", "following_list_450.xml").read_text()
    (debug / "following.xml").write_text(re.sub(r"\buser1\b", "followed.only", following))
    (debug / "broken.xml").write_text("<hierarchy><node")
    return data


def test_collect_reads_databases_media_dumps_and_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data: Path = _data_dir(tmp_path)
    env: Path = tmp_path / ".env"
    secret: Path = tmp_path / "ig_username"
    secret.write_text("file_owner\n")
    env.write_text("IG_USERNAME='env_owner'\n# IG_PASSWORD=x\n")
    env_file: Path = tmp_path / "other.env"
    env_file.write_text(f"IG_USERNAME_FILE={secret}\n")
    found: dict[str, str] = leak_scan.collect(
        [data, tmp_path / "missing"], [env, env_file, tmp_path / "no.env"]
    )
    assert found == {
        "real.poster": "username",
        "A long first line of a real caption": "caption",
        "Real Cafe": "place",
        "Cq1Ab2Cd3Ef": "shortcode",
        "Zz9Yy8Xx7Ww": "shortcode",
        "gone_since": "username",
        "avatar_owner": "username",
        "Qw1Er2Ty3Ui": "shortcode",
        "followed.only": "username",
        "env_owner": "username",
        "file_owner": "username",
    }
    assert "no data directory at" in capsys.readouterr().err


def test_a_dump_gives_only_personal_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Follower counts are promote-dump placeholders too, but not identifiers."""

    def identifiers(xml: str) -> dict[str, str]:
        return {
            "1,234 followers": "0 followers",
            "Real Display": "Display 1",
            "A note long enough to be searched for": "Note 1",
        }

    monkeypatch.setattr(leak_scan, "identifiers", identifiers)
    dump: Path = tmp_path / "dump.xml"
    dump.write_text("<hierarchy/>")
    assert leak_scan.from_dump(dump) == {
        "Real Display": "display name",
        "A note long enough to be searched for": "note",
    }


def test_an_unreadable_file_is_not_a_database(tmp_path: Path) -> None:
    assert not leak_scan._is_sqlite(tmp_path / "nope")


def test_the_allowlist_is_lowercased_and_skips_comments(tmp_path: Path) -> None:
    allow: Path = tmp_path / "allow.txt"
    allow.write_text("# a comment\n\nAbC  # trailing\nbob\n")
    assert leak_scan.allowlist(allow) == frozenset({"abc", "bob"})
    assert leak_scan.placeholder("user12", frozenset()) and leak_scan.placeholder("Place 3", frozenset())
    assert not leak_scan.placeholder("user", frozenset())


def test_a_username_matches_as_a_whole_word_in_any_case() -> None:
    scanner: Scanner = Scanner({"real.poster": "username", "Real Cafe": "place"}, ALLOWED)
    assert scanner.line("by REAL.poster at Real Cafe", "f:1") == [
        Finding("f:1", "username", "REAL.poster"),
        Finding("f:1", "place", "Real Cafe"),
    ]
    assert scanner.line("not real.poster_2 or xreal.poster or real cafe", "f:2") == []


def test_leak_shaped_text_is_flagged_unless_it_names_a_placeholder() -> None:
    scanner: Scanner = Scanner({}, ALLOWED)
    flagged: list[str] = [
        f.kind
        for f in scanner.line(
            "stranger posted a photo; stranger's story; stranger and 3 others;"
            " instagram.com/p/Real1234567/ instagram.com/stranger/",
            "f:1",
        )
    ]
    assert flagged == ["posted a", "'s story", "and N others", "post URL", "profile URL"]
    assert (
        scanner.line(
            "user3 posted a photo; the account's story; alice and 2 others; instagram.com/reel/TOP123/"
            " instagram.com/explore/tags/food/",
            "f:2",
        )
        == []
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A repository whose history added a leak that a later commit removed. Built once: the tests only
    read it. The git settings last as long as it does."""
    env: pytest.MonkeyPatch
    with pytest.MonkeyPatch.context() as env:
        name: str
        for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
            env.setenv(name, "test@example.invalid" if "EMAIL" in name else "test")
        env.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        root: Path = tmp_path_factory.mktemp("repo")
        _git(root, "init", "-q")
        (root / "notes.md").write_text("thanks to real.poster\nreal.poster again\n")
        (root / "image.bin").write_bytes(b"\0real.poster")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "one")
        (root / "notes.md").write_text("thanks to user1\n")
        _git(root, "commit", "-q", "-am", "two")
        yield root


def test_the_tree_and_the_history_are_searched(repo: Path) -> None:
    scanner: Scanner = Scanner({"real.poster": "username"}, ALLOWED)
    assert leak_scan.scan_tree(scanner, repo) == []
    findings: list[Finding] = leak_scan.scan_history(scanner, repo)
    assert [(f.where.split(" ", 1)[1], f.text) for f in findings] == [("notes.md", "real.poster")]


def test_main_reports_and_exits_1_on_a_finding(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(leak_scan, "ROOT", repo)
    data: Path = tmp_path / "data"
    _database(data / "db" / "posts.sqlite")
    exit_info: pytest.ExceptionInfo[SystemExit]
    with pytest.raises(SystemExit) as exit_info:
        leak_scan.main(["--data", str(data), "--env", str(tmp_path / "no.env")])
    assert exit_info.value.code == 1
    out: str
    err: str
    out, err = capsys.readouterr()
    assert re.search(r"^[0-9a-f]{12} notes\.md: username: real\.poster$", out, re.M)
    assert "searching for 6 identifiers (1 caption, 1 place, 2 shortcode, 2 username)" in err


def test_main_with_nothing_found_exits_0(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(leak_scan, "ROOT", repo)
    leak_scan.main(["--no-history"])
    out: str
    err: str
    out, err = capsys.readouterr()
    assert out == ""
    assert "searching for 0 identifiers and leak-shaped text" in err and "0 finding(s)" in err
