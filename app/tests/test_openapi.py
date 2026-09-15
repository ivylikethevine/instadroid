from pathlib import Path

import pytest
from jsonvalues import as_json

from tests.test_feed import json_at, make_app, parse_json

SPEC = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def test_committed_openapi_spec_matches_the_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEED_TOKEN", raising=False)
    make_app(tmp_path, monkeypatch)
    import app

    assert parse_json(SPEC.read_text()) == as_json(app.app.openapi()), (
        "docs/openapi.json is out of date: run python scripts/export_openapi.py"
    )


def test_spec_describes_the_json_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = parse_json(SPEC.read_text())
    health = json_at(spec, "paths", "/health", "get", "responses")
    assert json_at(health, "200", "content", "application/json", "schema") == {
        "$ref": "#/components/schemas/Health"
    }
    assert isinstance(health, dict) and "503" in health
    users = json_at(
        spec, "paths", "/users", "get", "responses", "200", "content", "application/json", "schema"
    )
    assert users == {"type": "array", "items": {"type": "string"}, "title": "Response Users Users Get"}
    content = json_at(spec, "paths", "/instagram.xml", "get", "responses", "200", "content")
    assert isinstance(content, dict) and "application/atom+xml" in content
