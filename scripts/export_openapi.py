"""Write the feed server's OpenAPI spec to docs/openapi.json, published with the project site.

    python scripts/export_openapi.py           # regenerate after changing a route in app/app.py
    python scripts/export_openapi.py --check   # exit 1 if the committed spec is out of date

tests/test_openapi.py runs the same comparison, so CI fails when a route changes without the spec.
Needs the app's requirements (run it from the dev venv).
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "openapi.json"


def render() -> str:
    """The spec as committed: importing app.py creates MEDIA_DIR, so point that somewhere harmless."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MEDIA_DIR"] = tmp
        os.environ["DB_PATH"] = str(Path(tmp) / "posts.sqlite")
        sys.path.insert(0, str(ROOT / "app"))
        import app

        return json.dumps(app.app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="fail instead of writing when the spec differs")
    spec = render()
    if parser.parse_args().check:
        if not SPEC.exists() or SPEC.read_text() != spec:
            print(f"{SPEC.relative_to(ROOT)} is out of date: run python scripts/export_openapi.py")
            return 1
        return 0
    SPEC.write_text(spec)
    print("wrote", SPEC.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
