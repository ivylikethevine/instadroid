"""The typing rules, from the test suite as well as scripts/check.sh. Every variable is annotated where
it's first bound (constricter, at pyproject.toml's [tool.constricter] level). And the escape hatches: ruff
bans importing Any and cast (TID251) and blanket ignores (PGH); basedpyright ignores `# type: ignore`
(enableTypeIgnoreComments=false) and reports Any values (reportAny). What's left is a suppression comment
that silences basedpyright itself, or a noqa that switches off the annotation rules (ruff's ANN or
constricter's LVA), and that's what this test catches."""

import re
from pathlib import Path

import pytest
from constricter.cli import command
from devtools import ROOT

SOURCES: list[Path] = sorted(
    p
    for top in ("app", "tests", "typings")
    for p in (ROOT / top).rglob("*.py*")
    if p.suffix in (".py", ".pyi") and "__pycache__" not in p.parts
)
FORBIDDEN: re.Pattern[str] = re.compile(
    r"#\s*(?:type:\s*ignore|(?:based)?pyright:\s*ignore|noqa:[^\n]*\b(?:ANN|LVA)\d*)"
)


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


def test_every_variable_is_annotated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same files and settings as the constricter check in scripts/check.sh's python group."""
    monkeypatch.chdir(ROOT)  # constricter reads [tool.constricter] from the nearest pyproject.toml
    status: int = command.main(["app", "tests", ".github/scripts"])
    assert status == 0, "every variable is annotated where it's first bound:\n" + capsys.readouterr().out
