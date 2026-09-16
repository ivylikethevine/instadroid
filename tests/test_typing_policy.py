"""The typing rules the linters can't enforce on their own. Every local variable is annotated where
it's first bound (devtools/local_annotations.py has the rule; nothing in ruff or basedpyright requires
it). And the escape hatches: ruff bans importing Any and cast
(TID251) and blanket ignores (PGH); basedpyright ignores `# type: ignore` (enableTypeIgnoreComments=false)
and reports Any values (reportAny). What's left is a suppression comment that silences basedpyright
itself, or a noqa that switches off the annotation rules, and that's what this test catches."""

import re
from pathlib import Path

from devtools import ROOT, local_annotations

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
    offences: list[str] = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in SOURCES
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if FORBIDDEN.search(line) and path.name != "test_typing_policy.py"
    ]
    assert not offences, "type-checker suppressions aren't allowed:\n" + "\n".join(offences)


def test_every_local_variable_is_annotated() -> None:
    """The same files and rule as the `local-annotations` script in scripts/check.sh's python group."""
    sources: list[Path] = local_annotations.repository_sources()
    assert len(sources) > 20
    offences: list[str] = [
        o.replace(str(ROOT) + "/", "") for path in sources for o in local_annotations.unannotated_locals(path)
    ]
    assert not offences, (
        "every local variable is annotated where it's first bound (see devtools/local_annotations.py):\n"
        + "\n".join(offences)
    )
