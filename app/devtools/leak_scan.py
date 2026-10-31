"""Search the repository, its working tree and every added line of its history, for real Instagram
identifiers: what a deployment has seen, and what no list can know.

    leak-scan                          # this machine's local/data and .env
    leak-scan --data /srv/a/local/data --data /mnt/b-copy/local/data --env /srv/a/.env
    leak-scan --no-history             # the working tree alone

A search only knows the identifiers in the data it's given, which is how a followed account's handle
outlasted the 2026-09-14 scan (docs/PROFILES.md), so give it every deployment's data directory (a copy
works). From each it collects the usernames, captions, places and shortcodes in every SQLite database
under it (backups and profile-dev scratch databases included, so accounts since unfollowed or pruned
still count), the account and shortcode names of its media files, and what promote-dump's scrubbing
finds in every raw dump under debug/ (names only in a Following list included). From each --env file,
the logged-in account's IG_USERNAME.

It also flags, with no list, text shaped like a leak: `<user> posted a`, `<user>'s story`, `<user> and
N others`, and an Instagram profile or post URL, unless the name or shortcode is a placeholder:
promote-dump's (`userN`, `Place N`, ...), or one listed in leak_allowlist.txt beside this file (the
fixtures' and tests' made-up names).

Prints each finding as `where: kind: text` and exits 1 if there was any. The findings quote what they
found, so keep the output out of issues and logs. Needs git; never touches a device.
"""

import argparse
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import BinaryIO

import igprofiles
from instadroid import versioning
from lxml import etree
from shared import sqlrows

from devtools import ROOT
from devtools.promote_dump import PLACEHOLDER_KINDS, identifiers

ALLOWLIST: Path = Path(__file__).with_name("leak_allowlist.txt")
# Shorter identifiers match ordinary words and code too often to be worth reporting.
MIN_LENGTH: int = 4
# A caption is searched for line by line, and only its lines this long: a short line ("Happy Friday!")
# is no more identifying than any other text.
MIN_CAPTION_LINE: int = 20
# Directories under a data directory that hold someone else's data, not the scraper's: the Android
# /data volume (Instagram's own databases), the APK cache and FreshRSS's copy of the feed.
_SKIP_DIRS: frozenset[str] = frozenset({"android", "apk", "freshrss"})
# (table, column, kind): the columns of the scraper's database (instadroid/db.py) that hold identifiers.
_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("posts", "username", "username"),
    ("posts", "caption", "caption"),
    ("posts", "place", "place"),
    ("posts", "id", "shortcode"),
    ("posts", "url", "shortcode"),
    ("accounts", "username", "username"),
    ("stories", "username", "username"),
    ("following", "username", "username"),
    ("following_pending", "username", "username"),
    ("rename_candidates", "old", "username"),
    ("rename_candidates", "new", "username"),
    ("rename_dismissals", "old", "username"),
    ("rename_dismissals", "new", "username"),
)
# An Instagram shortcode: what a permalink names a post by, and a stored post's id when it has one.
_SHORTCODE: re.Pattern[str] = re.compile(r"[A-Za-z0-9_-]{10,14}")
_PERMALINK: re.Pattern[str] = re.compile(r"instagram\.com/(?:p|reels?|tv)/([A-Za-z0-9_-]+)")
_HANDLE: str = r"[A-Za-z0-9_.]{2,30}"
# Leak-shaped text; group "id" is the account or shortcode it names.
type Shape = tuple[str, re.Pattern[str]]  # (kind, pattern)
_SHAPES: tuple[Shape, ...] = (
    ("posted a", re.compile(rf"(?<![\w.])(?P<id>{_HANDLE}) posted a\b")),
    ("'s story", re.compile(rf"(?<![\w.])(?P<id>{_HANDLE})'s story\b")),
    ("and N others", re.compile(rf"(?<![\w.])(?P<id>{_HANDLE}) and \d[\d,]* others?\b")),
    ("post URL", re.compile(r"instagram\.com/(?:p|reels?|tv)/(?P<id>[A-Za-z0-9_-]+)")),
    (
        "profile URL",
        re.compile(rf"instagram\.com/(?!(?:p|reels?|tv|explore|stories|accounts)/)(?P<id>{_HANDLE})"),
    ),
)
# promote-dump's placeholders: user1, Place 2, Display 3, ...
_PLACEHOLDER: re.Pattern[str] = re.compile("|".join(rf"{re.escape(p)}\d+" for p in PLACEHOLDER_KINDS))


@dataclass(frozen=True)
class Finding:
    where: str
    kind: str
    text: str


def _is_sqlite(path: Path) -> bool:
    f: BinaryIO
    try:
        with path.open("rb") as f:
            return f.read(16) == b"SQLite format 3\0"
    except OSError:
        return False


def _data_files(data_dir: Path) -> Iterator[Path]:
    """Every file under `data_dir`, leaving out _SKIP_DIRS at its top."""
    top: Path
    for top in sorted(data_dir.iterdir()):
        if top.is_dir() and top.name not in _SKIP_DIRS:
            yield from sorted(p for p in top.rglob("*") if p.is_file())
        elif top.is_file():
            yield top


def _add(found: dict[str, str], value: str, kind: str) -> None:
    """Record one identifier; a caption or note goes in line by line, its long lines only."""
    if kind not in ("caption", "note"):
        found.setdefault(value.strip(), kind)
        return
    line: str
    for line in value.splitlines():
        if len(line.strip()) >= MIN_CAPTION_LINE:
            found.setdefault(line.strip(), kind)


def from_database(path: Path) -> dict[str, str]:
    """{identifier: kind} from one scraper database, read-only; tables or columns it lacks are skipped."""
    found: dict[str, str] = {}
    con: sqlite3.Connection
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        table: str
        column: str
        kind: str
        for table, column, kind in _COLUMNS:
            columns: set[str | None] = {
                sqlrows.cell_str(r, "name")
                for r in sqlrows.fetch_all(con.execute(f"PRAGMA table_info({table})"))
            }
            if column not in columns:
                continue
            row: sqlite3.Row
            for row in sqlrows.fetch_all(con.execute(f"SELECT DISTINCT {column} FROM {table}")):
                value: str | None = sqlrows.cell_str(row, 0)
                if value is None:
                    continue
                link: re.Match[str]
                if column == "url":
                    for link in _PERMALINK.finditer(value):
                        _add(found, link.group(1), kind)
                elif kind != "shortcode" or _is_shortcode(value):
                    _add(found, value, kind)
    return found


def _is_shortcode(value: str) -> bool:
    """A shortcode, not a content hash (a post with no permalink is stored under its digest)."""
    return bool(_SHORTCODE.fullmatch(value)) and not re.fullmatch(r"[0-9a-f]+", value)


def from_media(path: Path, data_dir: Path) -> dict[str, str]:
    """{identifier: kind} from one media file's name: avatars/<username>.<ext>, and a post's
    <shortcode>[_<slide>].<ext>. Story crops are named by content hash and say nothing."""
    parts: tuple[str, ...] = path.relative_to(data_dir).parts
    if "avatars" in parts:
        return {path.stem: "username"}
    stem: str = re.sub(r"_\d+$", "", path.stem)
    if "media" in parts and "stories" not in parts and _is_shortcode(stem):
        return {stem: "shortcode"}
    return {}


@cache
def _newest_profile() -> igprofiles.BaseProfile:
    return igprofiles.load(igprofiles.available()[-1])


def from_dump(path: Path) -> dict[str, str]:
    """{identifier: kind} from one raw hierarchy dump: what promote-dump would scrub out of it, read
    under the newest profile, less the account's own follower counts. A file that isn't a hierarchy
    dump gives nothing."""
    try:
        with versioning.using(_newest_profile()):
            names: dict[str, str] = identifiers(path.read_text(errors="replace"))
    except etree.XMLSyntaxError:
        return {}
    found: dict[str, str] = {}
    value: str
    placeholder: str
    for value, placeholder in names.items():
        kind: str | None = PLACEHOLDER_KINDS.get(placeholder.rstrip("0123456789"))
        if kind:
            _add(found, value, kind)
    return found


def from_env(path: Path) -> dict[str, str]:
    """The logged-in account from an .env file: IG_USERNAME, or the file IG_USERNAME_FILE names."""
    found: dict[str, str] = {}
    line: str
    for line in path.read_text().splitlines():
        key: str
        value: str
        key, _, value = line.strip().partition("=")
        value = value.strip().strip("'\"")
        if key == "IG_USERNAME" and value:
            found[value] = "username"
        elif key == "IG_USERNAME_FILE" and value and Path(value).is_file():
            found[Path(value).read_text().strip()] = "username"
    return found


def collect(data_dirs: Sequence[Path], env_files: Sequence[Path]) -> dict[str, str]:
    """{identifier: kind} from every data directory and .env file given (a missing one is skipped),
    leaving out the too short, the purely numeric and the placeholders."""
    found: dict[str, str] = {}
    data_dir: Path
    for data_dir in data_dirs:
        if not data_dir.is_dir():
            print(f"leak-scan: no data directory at {data_dir}; skipped", file=sys.stderr)
            continue
        path: Path
        for path in _data_files(data_dir):
            if _is_sqlite(path):
                found.update(from_database(path))
            elif path.suffix == ".xml" and "debug" in path.relative_to(data_dir).parts:
                found.update(from_dump(path))
            else:
                found.update(from_media(path, data_dir))
    env_file: Path
    for env_file in env_files:
        if env_file.is_file():
            found.update(from_env(env_file))
    allowed: frozenset[str] = allowlist()
    return {
        value: kind
        for value, kind in found.items()
        if len(value) >= MIN_LENGTH and not value.isdigit() and not placeholder(value, allowed)
    }


def allowlist(path: Path = ALLOWLIST) -> frozenset[str]:
    """The made-up names and shortcodes the fixtures and tests use, lowercased, one per line; # starts a
    comment."""
    return frozenset(
        entry.lower()
        for entry in (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
        if entry
    )


def placeholder(value: str, allowed: frozenset[str]) -> bool:
    """One of promote-dump's placeholders (`userN`, `Place N`, ...), or a name the allowlist has."""
    return bool(_PLACEHOLDER.fullmatch(value)) or value.lower() in allowed


class Scanner:
    """Finds, in one line of text at a time, a known identifier, or leak-shaped text naming no
    placeholder."""

    def __init__(self, found: Mapping[str, str], allowed: frozenset[str]) -> None:
        self.allowed: frozenset[str] = allowed
        # Usernames match ignoring case (a handle is case-insensitive), so they're looked up lowercased.
        self.kinds: dict[str, str] = {
            (value.lower() if kind == "username" else value): kind for value, kind in found.items()
        }
        ordered: list[str] = sorted(found, key=len, reverse=True)  # "ab_c" before "ab"
        # Whole words only, so "ab" doesn't match inside "ab_c" or "ab.c".
        self.pattern: re.Pattern[str] | None = (
            re.compile(
                "|".join(
                    rf"(?<![\w.])(?i:{re.escape(value)})(?!\w)"
                    if found[value] == "username"
                    else rf"(?<![\w.]){re.escape(value)}(?!\w)"
                    for value in ordered
                )
            )
            if ordered
            else None
        )

    def line(self, text: str, where: str) -> list[Finding]:
        findings: list[Finding] = []
        m: re.Match[str]
        if self.pattern:
            for m in self.pattern.finditer(text):
                findings.append(
                    Finding(where, self.kinds.get(m.group(0)) or self.kinds[m.group(0).lower()], m.group(0))
                )
        shape: str
        shape_re: re.Pattern[str]
        for shape, shape_re in _SHAPES:
            for m in shape_re.finditer(text):
                if not placeholder(m.group("id"), self.allowed):
                    findings.append(Finding(where, shape, m.group(0)))
        return findings


def _git(*args: str, root: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=True, text=True, errors="replace"
    ).stdout


def scan_tree(scanner: Scanner, root: Path) -> list[Finding]:
    """Every tracked file, line by line; binary files are skipped."""
    findings: list[Finding] = []
    name: str
    for name in _git("ls-files", "-z", root=root).split("\0"):
        path: Path = root / name
        if not name or not path.is_file():
            continue
        data: bytes = path.read_bytes()
        if b"\0" in data:
            continue
        number: int
        line: str
        for number, line in enumerate(data.decode(errors="replace").splitlines(), 1):
            findings += scanner.line(line, f"{name}:{number}")
    return findings


def scan_history(scanner: Scanner, root: Path) -> list[Finding]:
    """Every line any commit on any ref added, reported once per commit and file. A finding still in
    the working tree shows up here too, under the commit that added it."""
    findings: list[Finding] = []
    seen: set[Finding] = set()
    commit: str = ""
    path: str = ""
    line: str
    for line in _git(
        "log", "--all", "-p", "-U0", "--no-color", "--no-ext-diff", "--format=commit %H", root=root
    ).splitlines():
        if line.startswith("commit "):
            commit = line.removeprefix("commit ")[:12]
        elif line.startswith("+++ "):
            path = line.removeprefix("+++ ").removeprefix("b/")
        elif line.startswith("+"):
            finding: Finding
            for finding in scanner.line(line[1:], f"{commit} {path}"):
                if finding not in seen:
                    seen.add(finding)
                    findings.append(finding)
    return findings


class Options(argparse.Namespace):
    data: list[Path] | None
    env: list[Path] | None
    history: bool


def main(argv: Sequence[str] | None = None) -> None:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--data",
        type=Path,
        action="append",
        help="a deployment's data directory (default: local/data); repeat",
    )
    ap.add_argument("--env", type=Path, action="append", help="a deployment's .env (default: .env); repeat")
    ap.add_argument("--no-history", dest="history", action="store_false", help="search the working tree only")
    opts: Options = ap.parse_args(argv, namespace=Options())
    found: dict[str, str] = collect(opts.data or [ROOT / "local" / "data"], opts.env or [ROOT / ".env"])
    kinds: dict[str, int] = {}
    kind: str
    for kind in found.values():
        kinds[kind] = kinds.get(kind, 0) + 1
    print(
        f"searching for {len(found)} identifiers"
        + (f" ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))})" if kinds else "")
        + " and leak-shaped text",
        file=sys.stderr,
    )
    scanner: Scanner = Scanner(found, allowlist())
    findings: list[Finding] = scan_tree(scanner, ROOT) + (scan_history(scanner, ROOT) if opts.history else [])
    finding: Finding
    for finding in findings:
        print(f"{finding.where}: {finding.kind}: {finding.text}")
    print(f"{len(findings)} finding(s)", file=sys.stderr)
    if findings:
        sys.exit(1)
