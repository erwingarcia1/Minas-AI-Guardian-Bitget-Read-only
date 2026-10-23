from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path

import pytest
import yaml

from sentinel.dashboard import serve_dashboard
from sentinel.edge import TRUSTED_PROXIES_SETTING, parse_trusted_proxies
from sentinel.metrics import Metrics
from sentinel.portal import serve_portal


@pytest.fixture
def stop():
    """Shuts a server down even when an assertion fails, so a leaked listener
    can't make the next test's bind flaky."""
    servers = []
    yield servers.append
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_dashboard_binds_loopback_by_default(db, cipher, stop):
    """G-36: the ops dashboard has no login of its own. On any default but
    loopback, following the quickstart publishes an unauthenticated
    'add customer' form - and every customer's contact details - on every
    interface of the machine it runs on."""
    server = serve_dashboard(db, cipher, 0)
    stop(server)
    assert server.server_address[0] == "127.0.0.1"


def test_portal_binds_loopback_by_default(db, stop):
    """The member portal has a login, but serves plain HTTP: its session
    cookie would cross the network in the clear. Reaching customers is the
    TLS proxy's job, not this socket's."""
    server = serve_portal(db, 0)
    stop(server)
    assert server.server_address[0] == "127.0.0.1"


def test_metrics_binds_loopback_by_default(stop):
    """The exposition is unauthenticated, and the styled view leaks
    operational shape - account counts, which rules are firing, which
    exchanges are misbehaving. That is reconnaissance, so it gets the same
    default as the two servers above."""
    server = Metrics.serve(0)
    stop(server)
    assert server.server_address[0] == "127.0.0.1"


def test_metrics_bind_address_is_configurable(stop):
    server = Metrics.serve(0, host="0.0.0.0")
    stop(server)
    assert server.server_address[0] == "0.0.0.0"


def test_dashboard_bind_address_is_configurable(db, cipher, stop):
    """Deployments that need a wider bind - a container, where Docker
    publishes by proxying to the container's interface and never to its
    loopback - must still be able to ask for one."""
    server = serve_dashboard(db, cipher, 0, host="0.0.0.0")
    stop(server)
    assert server.server_address[0] == "0.0.0.0"


def test_portal_bind_address_is_configurable(db, stop):
    server = serve_portal(db, 0, host="0.0.0.0")
    stop(server)
    assert server.server_address[0] == "0.0.0.0"


def test_default_dashboard_refuses_connections_from_off_box(db, cipher, stop):
    """The assertion above reads the address the server reports; this one
    proves the socket actually behaves that way. A loopback-bound listener is
    not reachable on the machine's routable address, which is the whole point
    of the default - the property a future refactor would silently lose."""
    server = serve_dashboard(db, cipher, 0)
    stop(server)
    port = server.server_address[1]

    routable = _routable_address()
    if routable is None:
        pytest.skip("host has no non-loopback IPv4 address to test against")

    with socket.socket() as probe:
        probe.settimeout(2)
        with pytest.raises(OSError):
            probe.connect((routable, port))


def _routable_address() -> str | None:
    """This machine's own non-loopback IPv4 address, without sending traffic:
    a connected UDP socket only fixes the local end of the tuple."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere
            addr = s.getsockname()[0]
        except OSError:
            return None
    return None if addr.startswith("127.") else addr


# --- the bind disclosure says what it means (#76) ----------------------------
#
# docker-compose deliberately binds the container wide and gates it with a
# 127.0.0.1: publish on the host - a boundary this process cannot see. The
# old startup warning read only the internal bind, so it fired on every
# correctly-secured deployment; a security warning that always fires is how
# a real one gets scrolled past. The deployment now DECLARES its boundary
# (SENTINEL_BIND_GATE), and the disclosure distinguishes three states
# instead of collapsing them into one cry of wolf.

def test_loopback_bind_needs_no_disclosure():
    from sentinel.cli import bind_disclosure
    assert bind_disclosure("127.0.0.1", 8390, None) == (None, None)
    assert bind_disclosure("127.0.0.1", 8390, "some gate") == (None, None)


def test_wide_bind_with_a_declared_gate_is_info_naming_gate_and_check():
    """Not silence, and not the warning: an INFO that names the declared
    boundary and the one host-side command that verifies it, because
    nothing in-process can see the host side of a container boundary -
    the disclosure says what to check rather than claiming safety."""
    from sentinel.cli import bind_disclosure
    level, message = bind_disclosure("0.0.0.0", 8390, "compose publishes 127.0.0.1:8390")
    assert level == "info"
    assert "compose publishes 127.0.0.1:8390" in message
    assert "cannot verify" in message
    assert "ss -tlnp" in message


def test_wide_bind_with_no_declared_gate_is_still_the_warning():
    """The real G-36 exposure: wide bind, no declared boundary. This case
    must stay loud - the fix for a warning that always fires is precision,
    never deletion."""
    from sentinel.cli import bind_disclosure
    level, message = bind_disclosure("0.0.0.0", 8390, None)
    assert level == "warning"
    assert "0.0.0.0" in message
    assert "SENTINEL_BIND_GATE" in message


def test_composes_wide_bind_declares_its_gate_and_the_publish_is_real():
    """The pairing (registration + execution, in the compose file): if
    docker-compose binds the dashboard wide, it must both declare the
    boundary (so startup can say who holds it) and actually contain the
    loopback publish the declaration describes. A declaration without its
    publish would soothe the log while the port faces the network."""
    from pathlib import Path

    import yaml

    compose = yaml.safe_load((Path(__file__).parent.parent / "docker-compose.yml").read_text())
    sentinel = compose["services"]["sentinel"]
    env = sentinel.get("environment", {})
    host = str(env.get("SENTINEL_DASHBOARD_HOST", "127.0.0.1")).strip()
    if host in ("127.0.0.1", "localhost", "::1"):
        return  # loopback container bind needs no gate
    gate = str(env.get("SENTINEL_BIND_GATE", "")).strip()
    assert gate, "wide container bind with no declared boundary (SENTINEL_BIND_GATE)"
    ports = [str(p) for p in sentinel.get("ports", [])]
    assert any(p.startswith("127.0.0.1:8390:") for p in ports), (
        "the declared gate promises a loopback publish for 8390 and the ports "
        "list does not contain one")


# --- the public edge: one proxy, and only the portal behind it (decision 22) ---
#
# Caddy is the first public listener this host has had (22b, 2026-10-23).
# What makes that safe is a handful of facts spread over two files -
# docker-compose.yml and deploy/caddy/Caddyfile - and each is held here,
# because a proxy config is read by nothing in the suite at runtime.

ROOT = Path(__file__).resolve().parent.parent
CADDYFILE = ROOT / "deploy/caddy/Caddyfile"


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def _caddy_lines() -> list[str]:
    """The Caddyfile with comments and blank lines removed."""
    out = []
    for raw in CADDYFILE.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def test_only_the_proxy_listens_publicly():
    """22c item 4: every publish in the compose file is on 127.0.0.1 except
    the proxy's two, which are exactly 80 and 443 over TCP (HTTP/3 is off,
    so nothing on UDP)."""
    public = {}
    for name, service in _compose()["services"].items():
        for port in service.get("ports", []):
            if not str(port).startswith("127.0.0.1:"):
                public.setdefault(name, []).append(str(port))
    assert public == {"caddy": ["80:80", "443:443"]}
    assert "protocols h1 h2" in _caddy_lines(), "HTTP/3 needs UDP 443, which nothing publishes"


def test_the_proxy_forwards_to_the_portal_and_nothing_else():
    """Caddy reaches the member portal - pages and /api/v1 - and never the
    ops dashboard (it stays on Tailscale Serve until 22e step 3) or the
    metrics endpoint (never published). The bind gate's declaration says
    so, and this is what makes the declaration true."""
    lines = _caddy_lines()
    env = _compose()["services"]["sentinel"]["environment"]
    portal = f"sentinel:{env['SENTINEL_PORTAL_PORT']}"
    upstreams = [[word for word in line.split()[1:] if word != "{"]
                 for line in lines if line.split()[0] == "reverse_proxy"]
    assert upstreams == [[portal]], f"the proxy's upstreams are {upstreams}, not the portal alone"
    assert "guardian.minastrade.com {" in lines
    text = "\n".join(lines)
    for never in (env["SENTINEL_DASHBOARD_PORT"], env["SENTINEL_METRICS_PORT"], "/admin"):
        assert never not in text, f"the Caddyfile mentions {never}"
    gate = env["SENTINEL_BIND_GATE"]
    assert "caddy" in gate and env["SENTINEL_PORTAL_PORT"] in gate, (
        "the bind gate no longer describes the public proxy it sits beside")


def test_the_proxy_makes_a_client_finish_what_it_starts():
    """One client holding a few hundred requests open - headers sent, body
    never - kept every thread the portal may start (pids_limit 256) busy:
    the portal, the app API and the healthcheck all went down (found in
    review, 2026-10-23). Caddy bounds how long a request may take to
    arrive, reads a whole body before it calls the portal, and never holds
    more than a quarter of the portal's threads."""
    words = [line.split() for line in _caddy_lines()]
    settings = {w[0]: w[1:] for w in words if w}
    assert settings.get("read_header") == ["10s"] and settings.get("read_body") == ["15s"]
    assert settings.get("request_buffers") == ["16KiB"]
    limit = int(settings["max_conns_per_host"][0])
    threads = _compose()["services"]["sentinel"]["pids_limit"]
    assert 0 < limit <= threads // 4, f"the proxy may hold {limit} of the portal's {threads} threads"


def test_the_portal_trusts_exactly_the_proxys_fixed_address():
    """22c item 6, the pairing: X-Forwarded-For is believed from caddy's
    address and no other, so caddy's address is fixed, on a network both
    services share, in a subnet compose controls, and outside the range
    Docker hands out - no other container can ever be given it."""
    compose = _compose()
    services = compose["services"]
    trusted = parse_trusted_proxies(services["sentinel"]["environment"][TRUSTED_PROXIES_SETTING])
    assert list(services["caddy"]["networks"]) == ["edge"]
    fixed = ipaddress.ip_address(services["caddy"]["networks"]["edge"]["ipv4_address"])
    assert trusted == {fixed}
    assert "edge" in services["sentinel"]["networks"]
    edge = compose["networks"]["edge"]
    assert edge.get("enable_ipv6") is False, "a connection over IPv6 would come from an address nobody trusts"
    [pool] = edge["ipam"]["config"]
    subnet = ipaddress.ip_network(pool["subnet"])
    dynamic = ipaddress.ip_network(pool["ip_range"])
    gateway = ipaddress.ip_address(pool["gateway"])
    assert fixed in subnet and dynamic.subnet_of(subnet)
    assert fixed not in dynamic and fixed != gateway
    # Clear of Docker's default pools and the default VPC (172.16.0.0/12
    # covers 172.17-172.31), Docker's second pool, and Tailscale's range.
    for taken in ("172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"):
        assert not subnet.overlaps(ipaddress.ip_network(taken)), f"{subnet} overlaps {taken}"


def test_the_proxy_image_is_pinned_and_its_container_hardened():
    """An exact release and its digest (a tag can be re-pushed, a digest
    cannot), and sentinel's posture as far as the image allows: its binary
    carries cap_net_bind_service, without which exec itself fails, so that
    one capability and no other comes back."""
    compose = _compose()
    caddy = compose["services"]["caddy"]
    pinned = re.fullmatch(r"caddy:(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}", caddy["image"])
    assert pinned, caddy["image"]
    assert caddy["cap_drop"] == ["ALL"] and caddy["cap_add"] == ["NET_BIND_SERVICE"]
    assert "no-new-privileges:true" in caddy["security_opt"]
    assert caddy["restart"] == "unless-stopped"
    assert {"./deploy/caddy:/etc/caddy:ro", "caddy-data:/data", "caddy-config:/config"} <= set(caddy["volumes"])
    assert {"caddy-data", "caddy-config"} <= set(compose["volumes"])
    # The validate command the Caddyfile documents names the same release.
    assert f"caddy:{pinned.group(1)} " in CADDYFILE.read_text()
