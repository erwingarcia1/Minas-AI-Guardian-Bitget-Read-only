"""The Google Play listing and Data safety answers (mobile/store/google-play.md).

The listing is one more place a person reads what the product is, so it
says what the landing page and the limits page say, word for word
(decision 25), and makes no prevention claim (#48). Google cuts text over
its limits, so they are checked here rather than discovered in the
console. And the Data safety answers are a statement about the app's
code: they were made against a list of runtime dependencies, and a new
one fails here until somebody has looked at the answers again.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from sentinel import portal
from tests.test_no_prevention_claims import claims_in

ROOT = Path(__file__).resolve().parent.parent
LISTING = ROOT / "mobile" / "store" / "google-play.md"


def _block(heading: str) -> str:
    """The first ```text block under a heading, before the next heading."""
    source = LISTING.read_text()
    match = re.search(rf"^#+ {re.escape(heading)}\n(?:(?!#)[^\n]*\n)*?```text\n(.*?)\n```",
                      source, re.M | re.S)
    assert match, f"no text block under '{heading}'"
    return match.group(1)


def test_each_text_is_inside_googles_limit():
    assert len(_block("App name")) <= 30
    assert len(_block("Short description")) <= 80
    assert len(_block("Full description")) <= 4000


def test_the_listing_says_what_the_landing_page_says():
    full = _block("Full description")
    for sentence in (portal.LANDING_STATEMENT, portal.LANDING_LEAD, portal.LANDING_SETUP,
                     *portal.LIMITS_HEADLINE):
        assert sentence in full, sentence
    for title, text in portal.LANDING_STEPS:
        assert f"{title} {text}" in full, title
    for title, text, _see_status in portal.LIMITS_CANNOT:
        assert f"{title} {text}" in full, title
    assert _block("App name") == "Minas AI Guardian"


def test_the_listing_makes_no_prevention_claim():
    for heading in ("App name", "Short description", "Full description"):
        for line in _block(heading).splitlines():
            assert claims_in(line) == [], line


def test_the_data_safety_answers_were_made_against_these_dependencies():
    """Decided 2026-10-26: no analytics, crash-reporting or advertising SDK,
    the app talks only to the Minas server. A new runtime dependency can
    change that, so adding one means reading the Data safety section again
    and updating the list in mobile/store/google-play.md."""
    package = json.loads((ROOT / "mobile" / "package.json").read_text())
    listed = set(_block("Runtime dependencies the Data safety answers were made against").split())
    assert set(package["dependencies"]) == listed, (
        "mobile/package.json's runtime dependencies changed: re-check the Data safety answers in "
        "mobile/store/google-play.md, then update its dependency list")
