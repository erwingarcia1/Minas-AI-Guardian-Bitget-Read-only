"""The app in mobile/ cannot drift from the server it belongs to (decision 21).

Read as files: the server's suite needs no Node to hold the app to the
portal's colours, the logo's derived files, the API's route set, the
landing page's sentences and the image's build context. The app's own
behaviour is tested by its own suite (Jest) in the Mobile workflow.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from sentinel import portal
from sentinel.api import ROUTES
from sentinel.portal import _PORTAL_STYLE

ROOT = Path(__file__).resolve().parent.parent
MOBILE = ROOT / "mobile"


def _portal_tokens() -> dict[str, str]:
    root = _PORTAL_STYLE[_PORTAL_STYLE.index(":root"):]
    root = root[:root.index("}")]
    return {name: value.strip() for name, value in re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", root)}


def _mobile_copies() -> dict[str, str]:
    """MOBILE_COPIES read out of scripts/brand-assets.py without importing it
    (it needs Pillow, which the service does not depend on)."""
    tree = ast.parse((ROOT / "scripts" / "brand-assets.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "MOBILE_COPIES":
            return ast.literal_eval(node.value)
    raise AssertionError("scripts/brand-assets.py no longer declares MOBILE_COPIES")


def test_the_apps_colours_are_the_portals():
    app = json.loads((MOBILE / "src" / "theme" / "tokens.json").read_text())
    portal = _portal_tokens()
    assert len(app) >= 20, "the scan found too few tokens to prove anything"
    drift = {k: (v, portal.get(k)) for k, v in app.items() if portal.get(k) != v}
    assert not drift, f"app token != portal :root token (app, portal): {drift}"


def test_the_apps_icons_are_the_brand_files_byte_for_byte():
    copies = _mobile_copies()
    assert copies, "no copies declared - the scan proves nothing"
    for source, name in copies.items():
        brand = (ROOT / "design" / "brand" / source).read_bytes()
        app = (MOBILE / "assets" / "images" / name).read_bytes()
        assert app == brand, f"mobile/assets/images/{name} drifted from design/brand/{source}: rerun derive"


def test_the_app_config_wears_them():
    expo = json.loads((MOBILE / "app.json").read_text())["expo"]
    images = {f"./assets/images/{name}" for name in _mobile_copies().values()}
    splash = next(p for p in expo["plugins"] if isinstance(p, list) and p[0] == "expo-splash-screen")[1]
    used = {expo["icon"], expo["android"]["adaptiveIcon"]["foregroundImage"],
            expo["android"]["adaptiveIcon"]["monochromeImage"], splash["image"]}
    assert used == images
    bg = _portal_tokens()["bg"]
    assert expo["backgroundColor"] == expo["android"]["adaptiveIcon"]["backgroundColor"] == splash["backgroundColor"] == bg
    assert expo["userInterfaceStyle"] == "dark"
    assert expo["name"] == "Minas AI Guardian"
    assert "expo-secure-store" in expo["plugins"], "the refresh token's storage (21c) is not configured"


def test_the_apps_identity_is_the_publishers_and_never_changes():
    """Decision 21f, answered 2026-10-23: the app is published as
    com.minastrade.guardian. The stores tie an app to this identifier for
    good - a change after the first upload is a different app, and every
    installed copy is orphaned - so it is pinned here."""
    expo = json.loads((MOBILE / "app.json").read_text())["expo"]
    assert expo["ios"]["bundleIdentifier"] == "com.minastrade.guardian"
    assert expo["android"]["package"] == "com.minastrade.guardian"


def test_the_generated_client_knows_exactly_the_servers_routes():
    """The Mobile workflow regenerates the file and fails on a difference;
    this is the server suite's own view of the same fact."""
    schema = (MOBILE / "src" / "api" / "schema.d.ts").read_text()
    paths = set(re.findall(r'^    "(/api/v1/[^"]*)": \{', schema, re.M))
    assert paths == {template for _, template in ROUTES}


def test_the_apps_activity_screen_makes_the_portals_promise():
    """Decision 25 and 6b: what the activity record promises about itself,
    including its one exception, reads the same on the web and the phone."""
    source = (MOBILE / "src" / "app" / "activity.tsx").read_text()
    assert f"'{portal.ACTIVITY_PROMISE}'" in source


def test_the_apps_landing_says_what_the_portals_does():
    """Decision 25: the app opens on the portal's landing page, in the
    portal's words. The sentences live in sentinel/portal.py; the app
    carries copies, because it cannot read the server before a sign-in,
    and this is what keeps the copies from drifting."""
    source = (MOBILE / "src" / "app" / "landing.tsx").read_text()
    # JSX text may spell an apostrophe as an entity; the portal's sentences do not.
    source = source.replace("&apos;", "'").replace("&#39;", "'")
    pinned = [portal.LANDING_STATEMENT, portal.LANDING_LEAD, portal.LANDING_SETUP, *portal.LIMITS_HEADLINE]
    for title, text in portal.LANDING_STEPS:
        pinned += [title, text]
    # The limits rows too: the app's limits screen reads them live from
    # GET /api/v1/limits, one tap away, so a copy here that lagged would be
    # two sentences for one fact inside one app.
    for title, text in portal.LIMITS_DOES:
        pinned += [title, text]
    for title, text, _to_status in portal.LIMITS_CANNOT:
        pinned += [title, text]
    assert len(pinned) == 23, "the scan found too few sentences to prove anything"
    missing = [sentence for sentence in pinned if sentence not in source]
    assert not missing, f"mobile/src/app/landing.tsx does not say, verbatim: {missing}"


def test_the_app_never_enters_the_server_image_build():
    patterns = [line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()]
    assert "mobile/" in patterns, "~900 MB of node_modules would join every docker build context"
