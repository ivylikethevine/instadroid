import igprofiles
import pytest
import scraper

V445 = igprofiles.load("v445")


@pytest.fixture(autouse=True)
def profile_v445(monkeypatch):
    """Every existing fixture and fake screen was captured from Instagram 445, so the suite as a
    whole is the v445 regression suite: pin that profile unless a test selects another itself."""
    monkeypatch.setattr(scraper, "PROFILE", V445)
    monkeypatch.setattr(scraper, "SELECTORS", V445.selectors)
    monkeypatch.setattr(scraper, "PROFILE_WARNING", None)
    monkeypatch.setattr(scraper, "IG_PROFILE", "")
    monkeypatch.setattr(scraper, "IG_APK_VERSION", "")
