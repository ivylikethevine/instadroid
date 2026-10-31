"""Disk use, shared by the scraper and the feed server."""

import os
from contextlib import suppress
from pathlib import Path


def tree_size(root: Path) -> tuple[int, int]:
    """(bytes, files) under `root`; zeros when it's missing. A file that vanishes mid-walk isn't counted."""
    size: int = 0
    count: int = 0
    names: list[str]
    folder: str
    for folder, _, names in os.walk(root):
        name: str
        for name in names:
            with suppress(OSError):
                size += (Path(folder) / name).stat().st_size
                count += 1
    return size, count
