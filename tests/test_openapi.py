from devtools.export_openapi import SPEC
from devtools.jsonvalues import JSON

from tests.support import json_at, parse_json

# That docs/openapi.json matches the routes is tests/test_scripts_cli.py's `export-openapi --check`.


def test_spec_describes_the_json_endpoints() -> None:
    spec: JSON = parse_json(SPEC.read_text())
    health: JSON = json_at(spec, "paths", "/health", "get", "responses")
    assert json_at(health, "200", "content", "application/json", "schema") == {
        "$ref": "#/components/schemas/Health"
    }
    assert isinstance(health, dict) and "503" in health
    users: JSON = json_at(
        spec, "paths", "/users", "get", "responses", "200", "content", "application/json", "schema"
    )
    assert users == {"type": "array", "items": {"type": "string"}, "title": "Response Users Users Get"}
    content: JSON = json_at(spec, "paths", "/instagram.xml", "get", "responses", "200", "content")
    assert isinstance(content, dict) and "application/atom+xml" in content
