"""Developer tools run on the host from the dev venv, never shipped in the image (app/.dockerignore).

    new-profile        version profile development: baseline, check, promote, validate, fork, restore
    promote-dump       a real hierarchy dump -> a scrubbed replay fixture
    check-new-builds   Instagram builds on APKPure newer than anything validated
    export-openapi     the feed server's spec -> docs/openapi.json

`pip install --no-deps -e .` from the repository root (after the hashed locks) installs these commands (pyproject.toml's
[project.scripts]); `python -m devtools.new_profile` works too.
"""

from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]  # the repository root: app/devtools/ -> app/ -> .
