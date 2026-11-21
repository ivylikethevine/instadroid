"""Measure tesseract's peak memory and time on story-sized frames, for STORY_OCR's budget inside the
app container's 256m limit.

Draws synthetic frames (no real story is read): a full 1080x2340 screen of text lines, the same over
photo-like noise, and a near-blank one, then runs `tesseract FILE stdout` on each as the scraper does
(one OpenMP thread) and prints its peak RSS from the kernel's rusage and its wall time. Run with any
Python that has Pillow, and tesseract on PATH (the host's, or inside the app image):

    local/.venv/bin/python scripts/scratch/ocr_memory.py [--lang eng] [--threads 1] [--out local/ocr-memory]
"""

import argparse
import os
import random
import resource
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH: int = 1080
HEIGHT: int = 2340
LINE: str = "Tonight at the gallery: doors 7pm, free entry, bring a friend"


def _text_frame(noise: bool) -> Image.Image:
    rng: random.Random = random.Random(1)
    img: Image.Image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    if noise:
        img = Image.effect_noise((WIDTH, HEIGHT), 80).convert("RGB")
    draw: ImageDraw.ImageDraw = ImageDraw.Draw(img)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default(size=44)
    y: int
    for y in range(80, HEIGHT - 80, 70):
        draw.text((40 + rng.randrange(20), y), LINE, fill="black", font=font)
    return img


def _blank_frame() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), (30, 30, 30))


def _measure(path: Path, lang: str, threads: int) -> tuple[int, float, int]:
    """(peak RSS in MiB, seconds, characters read) of one tesseract run on `path`."""
    env: dict[str, str] = {**os.environ, "OMP_THREAD_LIMIT": str(threads)}
    start: float = time.monotonic()
    proc: subprocess.Popen[bytes] = subprocess.Popen(
        ["tesseract", str(path), "stdout", "-l", lang],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    out: bytes = proc.communicate()[0]
    usage: resource.struct_rusage = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss over every child so far: each run is measured in a fresh interpreter by main() below.
    return usage.ru_maxrss // 1024, time.monotonic() - start, len(out.decode(errors="replace").strip())


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--lang", default="eng")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--out", default="local/ocr-memory")
    parser.add_argument("--one", help=argparse.SUPPRESS)  # measure a single file, in a child interpreter
    args: argparse.Namespace = parser.parse_args()
    if args.one:
        rss: int
        seconds: float
        chars: int
        rss, seconds, chars = _measure(Path(args.one), args.lang, args.threads)
        print(f"{rss}\t{seconds:.1f}\t{chars}")
        return 0
    out_dir: Path = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[str, Image.Image] = {
        "text": _text_frame(noise=False),
        "text-on-noise": _text_frame(noise=True),
        "blank": _blank_frame(),
    }
    print(f"{'frame':<14} {'peak RSS (MiB)':>15} {'time (s)':>9} {'characters':>11}")
    name: str
    img: Image.Image
    for name, img in frames.items():
        path: Path = out_dir / f"{name}.png"
        img.save(path)
        line: str = subprocess.run(
            [
                sys.executable,
                __file__,
                "--one",
                str(path),
                "--lang",
                args.lang,
                "--threads",
                str(args.threads),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        rss_s: str
        sec_s: str
        chars_s: str
        rss_s, sec_s, chars_s = line.split("\t")
        print(f"{name:<14} {rss_s:>15} {sec_s:>9} {chars_s:>11}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
