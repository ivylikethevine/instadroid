"""_ping_freshrss(): the best-effort GET that tells FreshRSS to refresh after a run stores
something new. Fully offline — urllib.request.urlopen is monkeypatched, nothing hits the network.
FRESHRSS_REFRESH_URL/_TIMEOUT are read from config at call time, so tests monkeypatch config."""

import urllib.error
import urllib.request

from instadroid import config, scrape


def test_disabled_when_freshrss_refresh_url_is_unset(monkeypatch):
    monkeypatch.setattr(config, "FRESHRSS_REFRESH_URL", "")
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or None)
    assert scrape._ping_freshrss(1, 0) is None
    assert calls == []


def test_no_op_when_nothing_new_was_stored(monkeypatch):
    monkeypatch.setattr(
        config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
    )
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or None)
    assert scrape._ping_freshrss(0, 0) is None
    assert calls == []


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"OK"


def test_fires_on_new_posts_and_appends_ajax_param(monkeypatch):
    monkeypatch.setattr(
        config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
    )
    monkeypatch.setattr(config, "FRESHRSS_REFRESH_TIMEOUT", 5.0)
    requested = {}

    def fake_urlopen(url, timeout=None):
        requested["url"] = url
        requested["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert scrape._ping_freshrss(2, 0) is None
    assert "ajax=1" in requested["url"]
    assert requested["timeout"] == 5.0


def test_fires_on_new_stories_only(monkeypatch):
    monkeypatch.setattr(
        config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert scrape._ping_freshrss(0, 1) is None


def test_does_not_duplicate_an_already_present_ajax_param(monkeypatch):
    monkeypatch.setattr(
        config,
        "FRESHRSS_REFRESH_URL",
        "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b&ajax=1",
    )
    requested = {}

    def fake_urlopen(url, timeout=None):
        requested["url"] = url
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    scrape._ping_freshrss(1, 0)
    assert requested["url"].count("ajax=") == 1


def test_connection_error_is_returned_not_raised(monkeypatch):
    monkeypatch.setattr(
        config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
    )

    def fake_urlopen(url, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = scrape._ping_freshrss(1, 0)
    assert result is not None
    assert "connection refused" in result


def test_log_and_error_redact_the_query_string_so_the_token_never_prints(monkeypatch, capsys):
    monkeypatch.setattr(
        config,
        "FRESHRSS_REFRESH_URL",
        "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=alice&token=SECRETTOKEN",
    )

    def fake_urlopen(url, timeout=None):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = scrape._ping_freshrss(1, 0)
    assert "SECRETTOKEN" not in result
    assert "SECRETTOKEN" not in capsys.readouterr().out
    assert "http://127.0.0.1:8080/i/" in result


def test_redact_url_drops_the_query_string():
    assert scrape._redact_url("http://127.0.0.1:8080/i/?a=1&token=secret") == "http://127.0.0.1:8080/i/"
