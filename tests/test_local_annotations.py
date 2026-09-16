"""The local-annotation checker itself (devtools/local_annotations.py): what it reports, what it
exempts, and its command line. tests/test_typing_policy.py runs it over the repository."""

import textwrap
from pathlib import Path

import pytest
from devtools import local_annotations

EXEMPT = """
import os
from os import path as p

G = 0


class Holder:
    count: int = 0

    def method(self, items: list[int]) -> int:
        total: int = 0
        for i in items:
            total += i
        return total


async def coroutine(items: list[str], *args: str, **kwargs: str) -> str:
    global G
    G = 1
    import re
    match items:
        case [first, *rest] if first:
            pass
        case {**others}:
            pass
        case str() as whole:
            pass
        case _:
            pass
    try:
        raise ValueError
    except ValueError as e:
        pass
    try:
        raise TypeError
    except* TypeError as eg:
        pass
    joined: str = ",".join(x for x in items)
    n: int
    more: list[int]
    n, *more = 1, 2, 3
    async for item in aiter(items):
        pass
    fh: object
    async with open(os.devnull) as fh:
        pass
    while (n := n - 1) > 0:
        pass
    m: re.Match[str] | None
    assert (m := re.match("x", joined)) or True

    def nested(value: int) -> int:
        inner: int = value + n
        return inner

    if joined:
        return joined
    else:
        return str(m)
"""

OFFENDING = """
def broken(items: list[int]) -> None:
    plain = 1
    a: int
    a, b = 1, 2
    first, *rest = items
    if (count := len(items)) > 0:
        pass
    with open("x") as fh:
        pass
    plain = 2
    a = count
"""


def _write(directory: Path, name: str, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path: Path = directory / name
    path.write_text(textwrap.dedent(source))
    return path


def test_exempt_bindings_and_declared_locals_pass(tmp_path: Path) -> None:
    assert local_annotations.unannotated_locals(_write(tmp_path, "clean.py", EXEMPT)) == []


def test_each_unannotated_first_binding_is_reported_once(tmp_path: Path) -> None:
    path: Path = _write(tmp_path, "broken.py", OFFENDING)
    assert local_annotations.unannotated_locals(path) == [
        f"{path}:3: plain = 1",
        f"{path}:5: a, b = 1, 2",
        f"{path}:6: first, *rest = items",
        f"{path}:6: first, *rest = items",
        f"{path}:7: if (count := len(items)) > 0:",
        f'{path}:9: with open("x") as fh:',
    ]


def test_repository_sources_are_the_checked_trees() -> None:
    sources: list[Path] = local_annotations.repository_sources()
    assert Path(__file__).resolve() in sources
    assert Path(local_annotations.__file__).resolve() in sources
    assert all(p.suffix == ".py" and "__pycache__" not in p.parts for p in sources)
    assert sources == sorted(sources)


def test_main_checks_the_given_files_and_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean: Path = _write(tmp_path, "clean.py", EXEMPT)
    broken: Path = _write(tmp_path / "pkg", "broken.py", OFFENDING)
    assert local_annotations.main([str(clean)]) == 0
    assert capsys.readouterr().out == "total 0 in 1 file(s)\n"
    assert local_annotations.main([str(tmp_path)]) == 1
    out: str = capsys.readouterr().out
    assert out.startswith(f"{broken}:3: plain = 1\n") and out.endswith("total 6 in 2 file(s)\n")


def test_main_without_arguments_checks_the_repository_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sources: list[Path] = [_write(tmp_path, "clean.py", EXEMPT), _write(tmp_path, "also_clean.py", EXEMPT)]
    monkeypatch.setattr(local_annotations, "repository_sources", lambda: sources)
    assert local_annotations.main([]) == 0
    assert capsys.readouterr().out == "total 0 in 2 file(s)\n"
