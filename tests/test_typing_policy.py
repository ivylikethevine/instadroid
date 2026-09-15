"""The typing escape hatches the linters can't forbid on their own. ruff bans importing Any and cast
(TID251) and blanket ignores (PGH); basedpyright ignores `# type: ignore` (enableTypeIgnoreComments=false)
and reports Any values (reportAny). What's left is a suppression comment that silences basedpyright
itself, or a noqa that switches off the annotation rules, and that's what this test catches."""

import re

from devtools import ROOT

SOURCES = sorted(
    p
    for top in ("app", "tests", "typings")
    for p in (ROOT / top).rglob("*.py*")
    if p.suffix in (".py", ".pyi") and "__pycache__" not in p.parts
)
FORBIDDEN = re.compile(r"#\s*(?:type:\s*ignore|(?:based)?pyright:\s*ignore|noqa:[^\n]*\bANN\d*)")


def test_there_are_sources_to_check() -> None:
    assert len(SOURCES) > 20


def test_no_type_checker_suppressions_or_annotation_noqa() -> None:
    offences = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in SOURCES
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if FORBIDDEN.search(line) and path.name != "test_typing_policy.py"
    ]
    assert not offences, "type-checker suppressions aren't allowed:\n" + "\n".join(offences)
