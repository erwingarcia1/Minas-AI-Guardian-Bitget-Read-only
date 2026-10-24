"""The answer every member path gives without a session (decision 25): a
303 to the landing page, and nothing else.

Before the landing page those paths answered the sign-in form in place, and
a dozen test files pinned `id="signin-title"` on them. They pin the redirect
now, through this one assertion, so the four facts it carries are asserted
the same way everywhere: the status, the Location, an empty body (no page
crosses the session boundary), and NO Set-Cookie - a stale cookie is the
landing page's to clear, because the landing page is what reads it to say
"You were signed out".
"""
from __future__ import annotations

import http.client
import urllib.parse


def exchange(host_port, method, path, body=None, cookie=None, host=None):
    """One request, with the whole response: (status, headers, body).
    `cookie` is a portal session token, `body` a form, `host` the Host
    header (decision 22d)."""
    address, port = host_port
    conn = http.client.HTTPConnection(address, port, timeout=5)
    headers = {}
    payload = None
    if body is not None:
        payload = urllib.parse.urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = f"minas_portal={cookie}"
    if host:
        headers["Host"] = host
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    message = resp.msg
    conn.close()
    return resp.status, message, data


def assert_sent_to_the_landing_page(host_port, method, path, body=None, cookie=None):
    """303, Location /, no body, no Set-Cookie - asserted in that order, so a
    route that quietly became a 404 or a 200 is named before anything else."""
    status, message, data = exchange(host_port, method, path, body=body, cookie=cookie)
    where = f"{method} {path} without a session"
    assert status == 303, f"{where} answered {status}, not the redirect to the landing page"
    assert message.get("Location") == "/", f"{where} redirected to {message.get('Location')!r}, not /"
    assert data == "", f"{where} carried a page body: nothing may cross the session boundary"
    assert message.get("Set-Cookie") is None, f"{where} set a cookie; a stale one is the landing page's to clear"
