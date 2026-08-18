from __future__ import annotations

import socket

import pytest

from sentinel.dashboard import serve_dashboard
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
