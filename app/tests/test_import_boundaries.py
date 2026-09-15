"""Import boundaries import-linter can't express because they concern single-file modules (the package
contracts live in pyproject.toml's [tool.importlinter]): the feed server runs as its own process and
must not pull in the scraper, and fileenv is the leaf both of them share."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
SCRAPER_SIDE = {"instadroid", "igprofiles", "scraper", "uiautomator2", "adbutils"}


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize(
    ("module", "forbidden"), [("app.py", SCRAPER_SIDE), ("fileenv.py", SCRAPER_SIDE | {"app"})]
)
def test_module_imports_nothing_from_the_scraper_side(module: str, forbidden: set[str]) -> None:
    assert not _imported_roots(APP / module) & forbidden
