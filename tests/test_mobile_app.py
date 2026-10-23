"""The app in mobile/ cannot drift from the server it belongs to (decision 21).

Read as files: the server's suite needs no Node to hold the app to the
portal's colours, the logo's derived files, the API's route set and the
image's build context. The app's own behaviour is tested by its own suite
(Jest) in the Mobile workflow.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

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


def test_the_app_never_enters_the_server_image_build():
    patterns = [line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()]
    assert "mobile/" in patterns, "~900 MB of node_modules would join every docker build context"
