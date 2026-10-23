"""The sign-in page and the one address (#88, decision 22d).

The anti-phishing story is "open the site yourself and sign in", and for
two months the page could not say WHICH site: there was no address worth
naming. Now there is one, and it lives in SENTINEL_PORTAL_URL and nowhere
else. Held here: with no address the page reads as it always did; with one
it names the host, as text, from the setting; a request addressed to any
other name - the old Funnel address - is told the address has changed; and
no module under sentinel/ carries the domain as a literal, so the setting
is the one place it exists (#88's own rule for whoever built this)."""
from __future__ import annotations

import ast
import http.client
import socket
import urllib.parse
from pathlib import Path

import pytest

from sentinel.edge import portal_host, portal_url, request_host
from sentinel.portal import _reset_url, serve_portal

ROOT = Path(__file__).resolve().parent.parent
# Not the real address, on purpose: the page must learn it from the setting.
ADDRESS = "https://guardian.example.test"
HOST = "guardian.example.test"
OLD = "minas-guardian.tail0fdd4a.ts.net:8443"
SHOWN = f'<strong style="color:var(--text)">{HOST}</strong>'


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def portal(db):
    port = _free_port()
    srv = serve_portal(db, port)
    yield port
    srv.shutdown()


def _login(port, host, method="GET", body=None, origin=None):
    """GET or POST /login addressed to `host`; None sends no Host header."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {}
    payload = None
    if body is not None:
        payload = urllib.parse.urlencode(body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Content-Length"] = str(len(payload))
    if origin is not None:
        headers["Origin"] = origin
    if host is None:
        conn.putrequest(method, "/login", skip_host=True)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders(payload)
    else:
        conn.request(method, "/login", body=payload, headers={"Host": host, **headers})
    resp = conn.getresponse()
    page = resp.read().decode()
    conn.close()
    return resp.status, page


# --- the copy ---------------------------------------------------------------

def test_without_an_address_the_page_reads_as_before(portal, monkeypatch):
    monkeypatch.delenv("SENTINEL_PORTAL_URL", raising=False)
    status, page = _login(portal, HOST)
    assert status == 200
    assert "Open the portal address you were given directly." in page
    assert "Always open this site and sign in" in page
    assert "Our only address" not in page
    assert "Our address has changed" not in page


def test_with_an_address_the_page_names_its_host_from_the_setting(portal, monkeypatch):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", ADDRESS + "/")
    status, page = _login(portal, HOST)
    assert status == 200
    assert f"Open {SHOWN} directly." in page
    assert f"Our only address is {SHOWN}." in page
    assert "address you were given" not in page
    assert "minastrade" not in page, "the host came from the setting, not from a literal"
    assert ADDRESS not in page, "the address is shown as a name to type, never as a link"
    assert f'href="https://{HOST}' not in page
    assert "Our address has changed" not in page, "the one address is not told to move"


# --- the old address --------------------------------------------------------

@pytest.mark.parametrize("name", [OLD, "localhost:8391", "127.0.0.1"])
def test_any_other_name_is_told_the_address_changed(portal, monkeypatch, name):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", ADDRESS)
    status, page = _login(portal, name)
    assert status == 200
    assert "Our address has changed." in page
    assert f"sign in at {SHOWN}" in page
    assert 'id="signin-title"' in page, "the notice is added to the sign-in page, nothing is taken from it"
    assert f"Our only address is {SHOWN}." in page


def test_a_failed_sign_in_on_the_old_address_carries_the_notice_too(portal, monkeypatch):
    """Every path that renders the sign-in page goes through the handler's
    `_login_page`, not the bare renderer. The wrong-password answer is the
    page a member on the old address sees most, so it is the one checked."""
    monkeypatch.setenv("SENTINEL_PORTAL_URL", ADDRESS)
    status, page = _login(portal, OLD, method="POST",
                          body={"email": "nobody@example.com", "password": "wrong"},
                          origin=f"https://{OLD}")
    assert status == 200
    assert "Invalid email or password." in page
    assert "Our address has changed." in page


def test_the_bare_renderer_has_one_caller():
    """The registration half of the test above: a new sign-in path that
    called `_render_login` directly would not know the request's host, and
    the old address would be told nothing on that path."""
    src = (ROOT / "sentinel/portal.py").read_text()
    assert src.count("def _render_login(") == 1
    assert src.count("_render_login(") == 2, "call it through Handler._login_page, which passes the request's host"


@pytest.mark.parametrize("name", ["Guardian.Example.test", "guardian.example.test:443", "GUARDIAN.EXAMPLE.TEST:8443"])
def test_the_name_is_compared_without_case_or_port(portal, monkeypatch, name):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", ADDRESS)
    status, page = _login(portal, name)
    assert status == 200
    assert "Our address has changed" not in page


@pytest.mark.parametrize("name", [None, "", "bad[host", "[::1"])
def test_a_missing_or_unreadable_host_header_gets_no_verdict(portal, monkeypatch, name):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", ADDRESS)
    status, page = _login(portal, name)
    assert status == 200, "an unreadable Host header is not an error"
    assert "Our address has changed" not in page
    assert f"Our only address is {SHOWN}." in page


# --- the setting ------------------------------------------------------------

def test_request_host_reads_the_name_only():
    assert request_host("Guardian.Example.test:8443") == "guardian.example.test"
    assert request_host("[::1]:8391") == "::1"
    assert request_host("127.0.0.1") == "127.0.0.1"
    assert request_host(" guardian.example.test ") == "guardian.example.test"
    assert request_host(None) is None
    assert request_host("") is None
    assert request_host("bad[host") is None


def test_portal_host_is_the_settings_hostname(monkeypatch):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", "https://Guardian.Example.test/")
    assert portal_url() == "https://Guardian.Example.test"
    assert portal_host() == "guardian.example.test"
    monkeypatch.setenv("SENTINEL_PORTAL_URL", "guardian.example.test:8443")  # written without its scheme
    assert portal_host() == "guardian.example.test"
    monkeypatch.setenv("SENTINEL_PORTAL_URL", "   ")
    assert portal_url() is None
    assert portal_host() is None
    monkeypatch.delenv("SENTINEL_PORTAL_URL")
    assert portal_host() is None


def test_reset_links_come_from_the_same_setting(monkeypatch):
    monkeypatch.setenv("SENTINEL_PORTAL_URL", ADDRESS + "/")
    assert _reset_url("t") == f"{ADDRESS}/reset?token=t"
    monkeypatch.delenv("SENTINEL_PORTAL_URL")
    assert _reset_url("t") == "/reset?token=t"


def test_the_domain_is_config_never_copy():
    """#88's rule: the domain string exists in exactly one place (config),
    never inline in copy. A docstring or comment may name it; no string
    the code could show or send may. The scan states its population first:
    it must see the domain in at least one docstring, or it is reading the
    wrong files, not a clean tree."""
    in_docstrings = 0
    in_code: list[str] = []
    for path in sorted((ROOT / "sentinel").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "minastrade.com" in node.value:
                if id(node) in docstrings:
                    in_docstrings += 1
                else:
                    in_code.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert in_docstrings, "the scan saw no docstring naming the domain - it is not reading sentinel/"
    assert not in_code, f"the domain is written into code instead of read from SENTINEL_PORTAL_URL: {in_code}"
