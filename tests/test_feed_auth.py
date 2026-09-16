"""FEED_TOKEN: auth on every path but /health, signed media URLs, token-carrying OPML, log redaction."""

import base64
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.feedclient import make_app, write_image

TOKEN = "s3cret-token"


class Credentials(TypedDict, total=False):
    """The ways a request can carry the token, as TestClient.get() keyword arguments."""

    headers: dict[str, str]
    params: dict[str, str]


def _auth_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FEED_TOKEN", TOKEN)
    return make_app(tmp_path, monkeypatch)


def _basic(user: str, password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture(autouse=True)
def no_token_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A FEED_TOKEN in the developer's own environment would otherwise leak into make_app()."""
    monkeypatch.delenv("FEED_TOKEN", raising=False)
    monkeypatch.delenv("FEED_TOKEN_FILE", raising=False)


def test_without_a_token_everything_stays_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    for path in ("/instagram.xml", "/stories.xml", "/opml", "/users", "/status", "/health"):
        assert client.get(path).status_code == 200, path
    assert "sig=" not in client.get("/instagram.xml").text


@pytest.mark.parametrize(
    "path", ["/instagram.xml", "/stories.xml", "/opml", "/users", "/status", "/openapi.json"]
)
def test_every_path_needs_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    client: TestClient = _auth_app(tmp_path, monkeypatch)
    r: Response = client.get(path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Basic realm="instadroid"'  # so a browser prompts


def test_health_stays_open_for_the_compose_healthcheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _auth_app(tmp_path, monkeypatch).get("/health").status_code == 200


@pytest.mark.parametrize(
    "kwargs",
    [
        {"headers": {"Authorization": f"Bearer {TOKEN}"}},
        {"headers": _basic("freshrss", TOKEN)},
        {"headers": _basic("", TOKEN)},
        {"params": {"token": TOKEN}},
    ],
)
def test_the_token_is_accepted_as_bearer_basic_or_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: Credentials
) -> None:
    assert _auth_app(tmp_path, monkeypatch).get("/instagram.xml", **kwargs).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Bearer {TOKEN}x"},
        _basic(TOKEN, "wrong"),  # the token as the username doesn't count
        {"Authorization": "Basic !!!not-base64"},
        {"Authorization": "Bearer "},
    ],
)
def test_wrong_or_malformed_credentials_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    assert _auth_app(tmp_path, monkeypatch).get("/instagram.xml", headers=headers).status_code == 401


def test_feed_media_urls_are_signed_and_load_without_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = _auth_app(tmp_path, monkeypatch)
    write_image(tmp_path / "media" / "ABC.jpg", (10, 10))
    body: str = client.get("/instagram.xml", params={"token": TOKEN}).text
    assert TOKEN not in body  # the feed content never carries the token itself
    from feedserver import auth

    sig: str = auth.media_sig("ABC.jpg")
    assert f"http://feed.test/media/ABC.jpg?sig={sig}" in body
    assert client.get(f"/media/ABC.jpg?sig={sig}").status_code == 200
    assert client.get("/media/ABC.jpg").status_code == 401
    assert client.get("/media/ABC.jpg?sig=" + "0" * 32).status_code == 401


def test_a_media_signature_only_opens_its_own_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = _auth_app(tmp_path, monkeypatch)
    write_image(tmp_path / "media" / "ABC.jpg", (10, 10))
    write_image(tmp_path / "media" / "other.jpg", (10, 10))
    from feedserver import auth

    assert client.get(f"/media/other.jpg?sig={auth.media_sig('ABC.jpg')}").status_code == 401


def test_opml_feed_urls_carry_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = _auth_app(tmp_path, monkeypatch)
    root: ET.Element[str] = ET.fromstring(
        client.get("/opml", headers={"Authorization": f"Bearer {TOKEN}"}).text
    )
    xml_urls: set[str | None] = {o.get("xmlUrl") for o in root.findall("./body/outline/outline")}
    assert xml_urls == {
        f"http://feed.test/instagram.xml?token={TOKEN}",
        f"http://feed.test/stories.xml?token={TOKEN}",
        f"http://feed.test/instagram.xml?user=someone&token={TOKEN}",
        f"http://feed.test/instagram.xml?user=other&token={TOKEN}",
    }


def test_feed_token_can_come_from_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret: Path = tmp_path / "feed_token"
    secret.write_text(TOKEN + "\n")
    monkeypatch.setenv("FEED_TOKEN_FILE", str(secret))
    client: TestClient = make_app(tmp_path, monkeypatch)
    assert client.get("/instagram.xml").status_code == 401
    assert client.get("/instagram.xml", params={"token": TOKEN}).status_code == 200


def test_access_log_blanks_the_token_and_still_skips_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_app(tmp_path, monkeypatch)
    from feedserver import auth

    def record(path: str) -> logging.LogRecord:
        args: tuple[str, str, str, str, int] = ("127.0.0.1:5000", "GET", path, "1.1", 200)
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', args, None)

    log_filter: auth.SkipHealthcheck = auth.SkipHealthcheck()
    feed: logging.LogRecord = record(f"/instagram.xml?user=a&token={TOKEN}&limit=5")
    assert log_filter.filter(feed)
    assert TOKEN not in feed.getMessage()
    assert "/instagram.xml?user=a&token=REDACTED&limit=5" in feed.getMessage()
    assert not log_filter.filter(record("/health"))
    assert log_filter.filter(record("/instagram.xml"))
    assert log_filter.filter(record("/instagram.xml?user=healthy_eats"))  # not a /health request
