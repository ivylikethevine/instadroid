import json
from pathlib import Path

import pytest

from tests.test_feed import make_app

SPEC = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def test_committed_openapi_spec_matches_the_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEED_TOKEN", raising=False)
    make_app(tmp_path, monkeypatch)
    import app

    assert json.loads(SPEC.read_text()) == app.app.openapi(), (
        "docs/openapi.json is out of date: run python scripts/export_openapi.py"
    )


def test_spec_describes_the_json_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = json.loads(SPEC.read_text())
    health = spec["paths"]["/health"]["get"]["responses"]
    assert health["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Health"}
    assert "503" in health
    users = spec["paths"]["/users"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert users == {"type": "array", "items": {"type": "string"}, "title": "Response Users Users Get"}
    assert "application/atom+xml" in spec["paths"]["/instagram.xml"]["get"]["responses"]["200"]["content"]
