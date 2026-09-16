#!/usr/bin/env python3
"""Docs that drift from the files they describe, read straight from the repository with the standard
library alone (the project's Python, 3.14, but none of its dependencies and no app imports):

- env: `.env.example` names every setting the shipped app reads (app/, app/entrypoint.sh) and every
  value docker-compose.yml interpolates, commented out or not, and nothing either doesn't read;
- contents: every Markdown file over CONTENTS_MIN_LINES lines has a `## Contents` block, and a block's
  top-level entries are its `##` headings in order, its nested entries existing deeper headings;
- links: a page the Pages site publishes doesn't link relatively into a path the site leaves out
  (the root _config.yml's `exclude:`, and any dot-path); those links need a github.com URL.

A script rather than a test under tests/: ci.yml's docs job, which runs on every pull request, calls
it through scripts/check.sh, while the Python jobs skip Markdown-only changes.

Usage: .github/scripts/docs_drift.py [env|contents|links]...   (no argument: all three)"""

import ast
import posixpath
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# A doc longer than this carries a `## Contents` block (CONTRIBUTING's docs conventions).
CONTENTS_MIN_LINES = 150


def _repo_files(*patterns: str) -> list[Path]:
    """Tracked and untracked-but-not-ignored files matching `patterns`, as CI's checkout would have them
    once committed; deleted files are left out."""
    result: subprocess.CompletedProcess[bytes]
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *patterns],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        sys.exit("docs_drift: needs git and a checkout to list the Markdown files")
    names: list[str] = sorted({name for name in result.stdout.decode().split("\0") if name})
    return [ROOT / name for name in names if (ROOT / name).is_file()]


# --- .env.example vs the settings the code reads -------------------------------------------------

_SETTING = re.compile(r"[A-Z][A-Z0-9_]*")
_SHELL_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
_EXAMPLE_LINE = re.compile(r"^#?([A-Z][A-Z0-9_]*)=", re.MULTILINE)
_COMPOSE_KEY = re.compile(r"^\s+([A-Z][A-Z0-9_]*):", re.MULTILINE)


def _captures(pattern: re.Pattern[str], text: str) -> list[str]:
    """Group 1 of every match, typed (findall's list isn't)."""
    return [match.group(1) for match in pattern.finditer(text)]


def _uncommented(text: str) -> str:
    """YAML or shell text without its comments (a `#` at a line's start or after whitespace)."""
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())


def _is_environ(node: ast.expr) -> bool:
    """`environ` or `<anything>.environ`: os.environ, or a function's `environ` mapping parameter."""
    return (isinstance(node, ast.Name) and node.id == "environ") or (
        isinstance(node, ast.Attribute) and node.attr == "environ"
    )


def _environ_key(node: ast.AST) -> ast.expr | None:
    """The key expression of an environment read (`environ.get(k)`, `environ[k]`, `getenv(k)`), if `node`
    is one."""
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
        return node.slice
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func: ast.expr = node.func
    if isinstance(func, ast.Attribute) and func.attr == "get" and _is_environ(func.value):
        return node.args[0]
    if (isinstance(func, ast.Name) and func.id == "getenv") or (
        isinstance(func, ast.Attribute) and func.attr == "getenv"
    ):
        return node.args[0]
    return None


def _called_name(node: ast.Call) -> str:
    func: ast.expr = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _app_settings() -> tuple[set[str], set[str]]:
    """(every variable the shipped app reads, the subset that also accepts NAME_FILE). A read is a
    literal key passed to the environment, or to a helper that reads its first parameter from it (a
    helper that also reads f"{name}_FILE" marks the NAME_FILE form). app/devtools/ isn't shipped in the
    image and sets variables rather than reading them."""
    trees: list[ast.Module] = [
        ast.parse(path.read_text())
        for path in (ROOT / "app").rglob("*.py")
        if "devtools" not in path.relative_to(ROOT / "app").parts
    ]
    helpers: dict[str, bool] = {}  # helper name -> reads NAME_FILE too
    key: ast.expr | None
    for tree in trees:
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or not fn.args.args:
                continue
            param: str = fn.args.args[0].arg
            keys: list[ast.expr] = [key for node in ast.walk(fn) if (key := _environ_key(node)) is not None]
            if any(isinstance(key, ast.Name) and key.id == param for key in keys):
                helpers[fn.name] = any(
                    isinstance(key, ast.JoinedStr) and ast.unparse(key) == f"f'{{{param}}}_FILE'"
                    for key in keys
                )
    names: set[str] = set()
    with_file: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            key = _environ_key(node)
            if key is None and isinstance(node, ast.Call) and node.args and _called_name(node) in helpers:
                key = node.args[0]
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and helpers[_called_name(node)]
                ):
                    with_file.add(key.value)
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and _SETTING.fullmatch(key.value):
                names.add(key.value)
    names.update(_captures(_SHELL_VAR, _uncommented((ROOT / "app" / "entrypoint.sh").read_text())))
    return names, with_file


def _documented() -> set[str]:
    return set(_captures(_EXAMPLE_LINE, (ROOT / ".env.example").read_text()))


def _compose_text() -> str:
    return _uncommented((ROOT / "docker-compose.yml").read_text())


def env_problems() -> list[str]:
    names: set[str]
    with_file: set[str]
    names, with_file = _app_settings()
    documented: set[str] = _documented()
    compose: str = _compose_text()
    interpolated: set[str] = set(_captures(_SHELL_VAR, compose))
    # Set by docker-compose.yml itself (an `environment:` key), not by the user.
    compose_set: set[str] = set(_captures(_COMPOSE_KEY, compose))
    known: set[str] = names | interpolated | {f"{name}_FILE" for name in with_file}
    return (
        [
            f".env.example: no NAME= or #NAME= line for {n}, which app/ reads"
            for n in sorted(names - documented - compose_set)
        ]
        + [
            f".env.example: no line for {n}, which docker-compose.yml interpolates"
            for n in sorted(interpolated - documented)
        ]
        + [
            f".env.example: {n} is read by neither app/ nor docker-compose.yml"
            for n in sorted(documented - known)
        ]
    )


# --- Contents blocks ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{2,6})\s+(.+?)\s*#*\s*$")
_ENTRY = re.compile(r"^(\s*)[-*]\s+\[.*\]\(#([^)\s]+)\)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _anchor(text: str) -> str:
    """GitHub's heading anchor: links reduced to their text, code and emphasis marks dropped, lowercased,
    anything but word characters, spaces and hyphens removed, spaces turned into hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "").replace("*", "")
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def _unfenced(lines: list[str]) -> list[tuple[int, str]]:
    """(line number, line) for every line outside a fenced code block."""
    kept: list[tuple[int, str]] = []
    fence: str = ""
    for number, line in enumerate(lines, 1):
        match: re.Match[str] | None = _FENCE.match(line)
        if match:
            if not fence:
                fence = match.group(1)
            elif match.group(1) == fence:
                fence = ""
            continue
        if not fence:
            kept.append((number, line))
    return kept


def _contents_problems(path: Path) -> list[str]:
    lines: list[str] = path.read_text().splitlines()
    body: list[tuple[int, str]] = _unfenced(lines)
    h2: list[str] = []
    anchors: set[str] = set()
    seen: Counter[str] = Counter()
    entries: list[tuple[bool, str]] = []  # (nested, anchor)
    in_contents: bool
    has_contents: bool
    in_contents = has_contents = False
    for _, line in body:
        heading: re.Match[str] | None = _HEADING.match(line)
        if heading:
            base: str = _anchor(heading.group(2))
            anchor: str = f"{base}-{seen[base]}" if seen[base] else base
            seen[base] += 1
            in_contents = len(heading.group(1)) == 2 and heading.group(2) == "Contents"
            if in_contents:
                has_contents = True
                continue
            anchors.add(anchor)
            if len(heading.group(1)) == 2:
                h2.append(anchor)
            continue
        entry: re.Match[str] | None = _ENTRY.match(line) if in_contents else None
        if entry:
            entries.append((bool(entry.group(1)), entry.group(2)))
    if not has_contents:
        return [f"{len(lines)} lines and no ## Contents"] if len(lines) > CONTENTS_MIN_LINES else []
    problems: list[str] = []
    top: list[str] = [anchor for nested, anchor in entries if not nested]
    if top != h2:
        problems.append(f"Contents lists {top}, but the ## headings are {h2}")
    problems += [
        f"Contents links #{a}, which is no heading" for nested, a in entries if nested and a not in anchors
    ]
    return problems


def contents_problems() -> list[str]:
    return [
        f"{path.relative_to(ROOT)}: {problem}"
        for path in _repo_files("*.md")
        for problem in _contents_problems(path)
    ]


# --- links the Pages site can't follow ---------------------------------------------------------

_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)")
_CODE_SPAN = re.compile(r"`[^`]*`")


def _site_exclusions() -> tuple[set[str], list[str]]:
    """(excluded files, excluded directory prefixes ending in /) from the root _config.yml's `exclude:`."""
    files: set[str] = set()
    dirs: list[str] = []
    inside: bool = False
    for line in (ROOT / "_config.yml").read_text().splitlines():
        if re.match(r"^exclude:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            break
        item: re.Match[str] | None = re.match(r"^\s+-\s+[\"']?([^\"'#\s]+)", line) if inside else None
        if item:
            entry: str = item.group(1).removeprefix("./")
            (dirs.append(entry) if entry.endswith("/") else files.add(entry))
    return files, dirs


def _off_site(rel: str, files: set[str], dirs: list[str]) -> str:
    """Why the repository path `rel` isn't published, or "" when it is."""
    if rel == ".." or rel.startswith("../"):
        return "climbs out of the repository"
    if any(part.startswith(".") for part in rel.split("/")):
        return "points into a dot-path, which Jekyll never publishes"
    if rel in files:
        return f"points at {rel}, excluded in _config.yml"
    for entry in dirs:
        if f"{rel}/".startswith(entry):
            return f"points under {entry}, excluded in _config.yml"
    return ""


def link_problems() -> list[str]:
    files: set[str]
    dirs: list[str]
    files, dirs = _site_exclusions()
    problems: list[str] = []
    for path in _repo_files("*.md"):
        rel_file: str = path.relative_to(ROOT).as_posix()
        if _off_site(rel_file, files, dirs):
            continue  # not a page on the site, so its links never render there
        base: str = posixpath.dirname(rel_file)
        for number, line in _unfenced(path.read_text().splitlines()):
            line = _CODE_SPAN.sub("", line)
            targets: list[str] = _captures(_LINK, line) + _captures(_REFERENCE, line)
            for target in targets:
                target = target.split("#", 1)[0].split("?", 1)[0]
                if not target or ":" in target or target.startswith("/"):
                    continue
                why: str = _off_site(posixpath.normpath(posixpath.join(base, target)), files, dirs)
                if why:
                    problems.append(f"{rel_file}:{number}: {target} {why}; use a github.com URL")
    return problems


CHECKS = {"env": env_problems, "contents": contents_problems, "links": link_problems}


def main(argv: list[str]) -> int:
    unknown: list[str] = [name for name in argv if name not in CHECKS]
    if unknown:
        print(
            f"usage: docs_drift.py [{'|'.join(CHECKS)}]...  (unknown: {' '.join(unknown)})", file=sys.stderr
        )
        return 2
    failed: int = 0
    for name in argv or list(CHECKS):
        problems: list[str] = CHECKS[name]()
        for problem in problems:
            print(f"  {problem}")
        print(f"docs_drift {name}: {'FAIL, ' + str(len(problems)) + ' problem(s)' if problems else 'OK'}")
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
