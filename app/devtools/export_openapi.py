"""Write the feed server's OpenAPI spec to docs/openapi.json, published with the project site.

    export-openapi           # regenerate after changing a route in app/feedserver/
    export-openapi --check   # exit 1 if the committed spec is out of date

tests/test_scripts_cli.py runs the same --check, so CI fails when a route changes without the spec.
Needs the app's requirements (run it from the dev venv).
"""

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from devtools import ROOT
from devtools.jsonvalues import JSON

SPEC = ROOT / "docs" / "openapi.json"


def render() -> str:
    """The spec as committed: importing feedserver creates MEDIA_DIR, so point that somewhere harmless."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MEDIA_DIR"] = tmp
        os.environ["DB_PATH"] = str(Path(tmp) / "posts.sqlite")
        import feedserver

        spec: dict[str, JSON] = feedserver.app.openapi()
        return json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


class Options(argparse.Namespace):
    check: bool


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="fail instead of writing when the spec differs")
    spec = render()
    if parser.parse_args(argv, namespace=Options()).check:
        if not SPEC.exists() or SPEC.read_text() != spec:
            print(f"{SPEC.relative_to(ROOT)} is out of date: run export-openapi")
            return 1
        return 0
    SPEC.write_text(spec)
    print("wrote", SPEC.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
