import pytest
import scraper
from igprofiles import V445


@pytest.fixture(autouse=True)
def selector_profile_445(monkeypatch):
    """Every existing fixture and fake screen was captured from Instagram 445, so the suite as a
    whole is the 445 regression suite: pin that profile unless a test activates another itself."""
    monkeypatch.setattr(scraper, "PROFILE", V445)
    monkeypatch.setattr(scraper, "SELECTORS", V445.selectors)
    monkeypatch.setattr(scraper, "PROFILE_WARNING", None)
    monkeypatch.setattr(scraper, "IG_SELECTOR_PROFILE", "")
