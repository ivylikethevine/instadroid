"""Run the story-reshare check (app/instadroid/reshare.py) over a deployment's stored stories, read-only.

Prints each story's best post match, its correlation and whether it would be dropped, and writes contact
sheets of the dropped and the matched-but-kept stories to local/reshare-census/. Every post in the
database is a candidate, whichever was stored first, so this overstates what a live run drops.

    local/.venv/bin/python scratch/reshare_census.py [DATA_ROOT]

DATA_ROOT (default local/private) holds db/posts.sqlite and media/.
"""

import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app"))

from instadroid import reshare  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

root = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "local/private"
out = REPO / "local/reshare-census"
out.mkdir(parents=True, exist_ok=True)
con = sqlite3.connect(f"file:{root / 'db/posts.sqlite'}?mode=ro", uri=True)

posts = []
for post_id, name in con.execute(
    "SELECT id, media_file FROM posts WHERE media_file IS NOT NULL UNION ALL SELECT post_id, file FROM media"
):
    path = root / "media" / name
    if path.exists():
        with Image.open(path) as img:
            if post := reshare.post_image(post_id, img):
                posts.append(post)

stories = con.execute("SELECT id, username, media_file FROM stories ORDER BY scraped_at").fetchall()
dropped, kept = [], []
started = time.time()
for n, (story_id, username, name) in enumerate(stories):
    path = root / "media" / name
    if not path.exists():
        continue
    with Image.open(path) as img:
        frame = reshare.Frame(img)
        thumb = img.convert("RGB").resize((180, round(180 * img.height / img.width)))
    if found := reshare.find_reshared(frame, posts):
        post, card = found
        plain = frame.plain_around(card, post.fine.height / post.fine.width)
        (dropped if plain else kept).append((n, card, thumb, post.fine.height / post.fine.width))
        print(f"{n:4d} {story_id[:8]} {card.score:.2f} scale {card.scale:.2f} {'DROP' if plain else 'keep'}")
print(
    f"{len(stories)} stories, {len(posts)} post images: {len(dropped)} dropped, {len(kept)} matched and kept,"
    f" {(time.time() - started) / max(len(stories), 1):.2f} s per story"
)

for label, group in (("dropped", dropped), ("kept", kept)):
    if not group:
        continue
    cols = 10
    width, height = group[0][2].size
    sheet = Image.new("RGB", (width * cols, (height + 14) * -(-len(group) // cols)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (n, card, thumb, aspect) in enumerate(group):
        x, y = (i % cols) * width, (i // cols) * (height + 14)
        sheet.paste(thumb, (x, y + 14))
        left = (1 - card.scale) / 2 * width
        top = y + 14 + card.top * width
        draw.rectangle((x + left, top, x + width - left, top + card.scale * aspect * width), outline=(0, 255, 0))
        draw.text((x + 2, y + 1), f"{n} {card.score:.2f}", fill="black")
    sheet.save(out / f"{label}.jpg", quality=82)
    print(f"wrote {out / label}.jpg")
