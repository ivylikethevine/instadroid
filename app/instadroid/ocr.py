"""Story text: tesseract reads each stored story frame once, at the end of a run, when STORY_OCR is on.
The text lands in stories.text (NULL until read, "" when there was none), which the stories feed shows
and /browse searches through stories_fts.

tesseract runs as a child of the scraper, inside the app container's memory limit, with one OpenMP
thread; its peak in the image is 57-72MiB on a full-screen frame, most of it the language model
(scripts/scratch/ocr_memory.py measures it)."""

import os
import re
import sqlite3
import subprocess
from pathlib import Path

from shared import sqlrows

from . import config
from .common import log

TIMEOUT: float = 60.0  # seconds for one frame; a full-screen frame takes about one
MAX_PER_RUN: int = 20  # a backlog (STORY_OCR turned on over stored stories) drains this many a run
MIN_WORD_CHARS: int = 2  # what's left of a line once single characters (OCR noise) are dropped


def clean(raw: str) -> str:
    """tesseract's output as story text: one space between words, lines kept, and lines of noise (no
    word of MIN_WORD_CHARS letters or digits) dropped."""
    lines: list[str] = []
    line: str
    for line in raw.splitlines():
        words: list[str] = line.split()
        if any(len(re.sub(r"\W", "", w)) >= MIN_WORD_CHARS for w in words):
            lines.append(" ".join(words))
    return "\n".join(lines)


def read_text(path: Path) -> str | None:
    """The text on an image file, cleaned; None when tesseract can't run or fails on it."""
    env: dict[str, str] = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    try:
        done: subprocess.CompletedProcess[str] = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", config.STORY_OCR_LANG],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=TIMEOUT,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        log("WARN: STORY_OCR is on but the image has no tesseract: rebuild it (`docker compose build`)")
        return None
    except (OSError, subprocess.TimeoutExpired) as e:
        log("WARN: story OCR could not run:", repr(e))
        return None
    if done.returncode != 0:
        log(f"WARN: story OCR failed on {path.name}: {done.stderr.strip()[-200:]}")
        return None
    return clean(done.stdout)


def read_stories(con: sqlite3.Connection, limit: int = MAX_PER_RUN) -> int:
    """Read the text of up to `limit` stored stories not read yet, newest first, when STORY_OCR is on.
    Returns how many were read. A frame tesseract fails on stays unread, for the next run; one whose file
    is gone is marked read with no text."""
    if not config.STORY_OCR:
        return 0
    rows: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute(
            "SELECT id, media_file FROM stories WHERE text IS NULL ORDER BY scraped_at DESC LIMIT ?", (limit,)
        )
    )
    done: int = 0
    row: sqlite3.Row
    for row in rows:
        media_file: str | None = sqlrows.cell_str(row, 1)
        path: Path | None = config.MEDIA_DIR / media_file if media_file else None
        found: str | None = read_text(path) if path and path.is_file() else ""
        if found is None:
            break  # tesseract itself is failing: the rest would too
        con.execute("UPDATE stories SET text = ? WHERE id = ?", (found, sqlrows.must_str(row, 0)))
        con.commit()
        done += 1
    if done:
        log(f"story OCR: read {done} frame(s)")
    return done
