"""What both web servers must know about the hop in front of them (decision 22).

The member portal is reached at https://guardian.minastrade.com through
Caddy, one more compose service (22b, answered 2026-10-23). Two rules about
that hop live here, shared by the portal and the ops dashboard so the two
surfaces cannot drift apart.

**Who is asking (22c item 6).** Behind a proxy every TCP connection comes
from the proxy. A server that reads the client address off the socket then
puts every visitor in one login-throttle bucket: five wrong passwords from
anyone block every member's sign-in, the app's included, for fifteen
minutes. So the address comes from `X-Forwarded-For` - but only when the
TCP peer is a proxy named in SENTINEL_TRUSTED_PROXIES, and only the
right-most address in it that is not itself a trusted proxy. Caddy writes
the address it saw last (it replaces the header outright unless it is told
to trust a proxy in front of it, and then appends); anything to the left
of that came from the client and says whatever the client typed. From any
other peer the header is ignored entirely: a header is a claim, and only
our own proxy's claim is worth believing.

**What every response says (22c item 3).** HSTS, and clickjacking
protection twice over - `Content-Security-Policy: frame-ancestors 'none'`
for current browsers, `X-Frame-Options: DENY` for older ones - on every
response either server sends: pages, redirects, errors, the logo files,
/healthz and the JSON API. Set here, in the application, where a test pins
them, rather than only at the proxy, whose configuration no test exercises.
"""
from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

from sentinel.config import env_setting

logger = logging.getLogger("sentinel.edge")

TRUSTED_PROXIES_SETTING = "SENTINEL_TRUSTED_PROXIES"
# The dashboard's own, separate on purpose, so the two surfaces can sit
# behind different hops without one setting speaking for the other. Under
# docker compose both name the same address - Caddy forwards /admin and
# /admin/* to the dashboard (22e step 3) and everything else to the portal -
# and a test holds the two equal. Unset trusts no proxy: the bare `sentinel
# run` and the demo server have none in front of them.
DASHBOARD_TRUSTED_PROXIES_SETTING = "SENTINEL_DASHBOARD_TRUSTED_PROXIES"

# One year. No includeSubDomains and no preload: this service answers for
# guardian.minastrade.com alone, and the other minastrade.com hosts are not
# ours to bind to HTTPS for a year. Over plain HTTP - the SSH tunnel to
# localhost, the demo server - browsers ignore the header (RFC 6797 8.1),
# so it cannot pin a development address.
HSTS = "max-age=31536000"
FRAME_ANCESTORS = "frame-ancestors 'none'"
X_FRAME_OPTIONS = "DENY"

# Headers only the edge sets. A route that sends one is overruled, never
# doubled: two values for either header is a conflict browsers resolve in
# their own ways, and a route has no business weakening them.
_EDGE_OWNED = frozenset({"strict-transport-security", "x-frame-options"})

Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def _address(text: str) -> Address | None:
    """`text` as one IP address, or None if it is not exactly one. An
    IPv4-mapped IPv6 address is read as the IPv4 address it carries, so one
    client cannot be two."""
    try:
        parsed = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def parse_trusted_proxies(raw: str | None, setting: str = TRUSTED_PROXIES_SETTING) -> frozenset[Address]:
    """SENTINEL_TRUSTED_PROXIES - or the dashboard's own, named by
    `setting` so the log blames the right one: exact addresses,
    comma-separated.

    None - unset, or blank, which `env_setting` reads as unset - trusts no
    proxy, and the client address is the TCP peer: the behaviour from before
    the proxy existed. An entry that is not one exact address (a name, a
    CIDR range, a typo) is refused and named in the log, and the others
    stand. Refusing is the safe direction: an entry that cannot be read can
    only ever make the server trust LESS, and the cost of that is the shared
    throttle bucket this setting exists to remove - loud in the log, not a
    hole."""
    if raw is None:
        return frozenset()
    trusted: set[Address] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        address = _address(entry)
        if address is None:
            logger.error("%s: %r is not an IP address - ignored. Exact addresses only: "
                         "no names, no ranges.", setting, entry)
            continue
        trusted.add(address)
    return frozenset(trusted)


def trusted_proxies_from_env(surface: str, setting: str = TRUSTED_PROXIES_SETTING) -> frozenset[Address]:
    """`setting`, read once when `surface` starts, and said in the log -
    what this server believes is a startup fact an operator can check."""
    trusted = parse_trusted_proxies(env_setting(setting), setting=setting)
    if trusted:
        logger.info("%s: X-Forwarded-For is believed from %s only", surface,
                    ", ".join(sorted(str(a) for a in trusted)))
    else:
        logger.info("%s: no trusted proxy (%s names none) - the client address is the TCP peer",
                    surface, setting)
    return trusted


def client_address(peer: str | None, forwarded_for: list[str] | None,
                   trusted: frozenset[Address]) -> str:
    """The address a request came from, for the throttles, logs and audit
    rows that key on it.

    `peer` is the TCP peer's address, or None if there is none.
    `forwarded_for` is every X-Forwarded-For field line in the order
    received, or None: several lines are one list (RFC 9110 5.3), and the
    right-most entry is in the last line - reading only the first line
    would hand the choice to whoever wrote it.

    - Peer not a trusted proxy: the peer, and the header is never read.
    - Peer trusted: walk the list from the right, past every trusted
      proxy; the first address that is not one is the client.
    - An entry that is not an address where the walk needs one: the peer.
      Never anything further left, which the client wrote. The cost is that
      such requests share the proxy's bucket; Caddy never sends one.
    - No header, or nothing but trusted proxies in it: the peer."""
    if peer is None:
        return "?"
    peer_address = _address(peer)
    if peer_address is None or peer_address not in trusted:
        return peer
    hops = [hop.strip() for line in forwarded_for or [] for hop in line.split(",")]
    for hop in reversed(hops):
        if not hop:
            continue  # an empty list element is no hop (RFC 9110 5.6.1)
        address = _address(hop)
        if address is None:
            logger.warning("X-Forwarded-For from trusted proxy %s carries %r where an address belongs "
                           "- using the proxy's own address", peer, hop[:64])
            return peer
        if address in trusted:
            continue
        return str(address)
    return peer


# The portal's one public address (decision 22, #88). Read per call, like
# every reader of the setting before it, so a test can set it without
# restarting the server and nothing on the box has to restart to learn it.
PORTAL_URL_SETTING = "SENTINEL_PORTAL_URL"


def portal_url() -> str | None:
    """`SENTINEL_PORTAL_URL` with no trailing slash, or None while the box
    has no public address: the base of every link the product writes."""
    url = env_setting(PORTAL_URL_SETTING)
    return url.rstrip("/") if url else None


def _hostname(authority: str) -> str | None:
    """The host of `authority` - `host`, `host:port` or `[v6]:port` -
    lower-cased, or None if it cannot be read as one."""
    try:
        return urlsplit("//" + authority.strip()).hostname or None
    except ValueError:  # an unbalanced IPv6 bracket
        return None


def portal_host() -> str | None:
    """The hostname of portal_url(): the one string the anti-phishing copy
    may name. #88's rule is that the domain exists in exactly one place,
    the setting, and never inline in copy; this is how copy gets it. None
    while there is no address. (Not SENTINEL_PORTAL_HOST, which is the
    interface the raw :8391 server binds.)"""
    url = portal_url()
    if url is None:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host or _hostname(url)  # a setting written without its scheme


def request_host(host_header: str | None) -> str | None:
    """The name a request was addressed to, from its Host header, or None
    when there is none or it cannot be read. Caddy passes the client's Host
    through, so behind it this is the name the visitor typed; against
    portal_host() it tells an old address from the one address (22d)."""
    if not host_header:
        return None
    return _hostname(host_header)


def with_frame_ancestors(policy: str | None) -> str:
    """One Content-Security-Policy: `policy`'s directives, with
    frame-ancestors 'none' in place of any frame-ancestors it had."""
    if policy is None:
        return FRAME_ANCESTORS
    kept = [d.strip() for d in policy.split(";")
            if d.strip() and d.split()[0].lower() != "frame-ancestors"]
    return "; ".join([*kept, FRAME_ANCESTORS])


class EdgeHandler:
    """Both rules, for a BaseHTTPRequestHandler subclass. List it BEFORE
    BaseHTTPRequestHandler in the bases, so these methods wrap the standard
    library's; the server that builds the handler sets `trusted_proxies`
    once, at startup.

    The headers are added in `end_headers`, the one call every response
    makes - `send_error` included - so a response path added later cannot
    forget them. Both servers speak HTTP/1.0 (the library default), so no
    interim 1xx response is ever sent to carry them by mistake."""

    trusted_proxies: frozenset[Address] = frozenset()
    _route_csp: str | None = None

    def _client_ip(self) -> str:
        peer = self.client_address[0] if self.client_address else None
        return client_address(peer, self.headers.get_all("X-Forwarded-For"), self.trusted_proxies)

    def _peer_is_trusted_proxy(self) -> bool:
        """Whether this connection arrived through a proxy the setting
        names - the public name, via Caddy - rather than straight from the
        client, as the SSH tunnel's do. For a page deciding what it may say
        to the public that it would say to the owner on the tunnel."""
        peer = self.client_address[0] if self.client_address else None
        address = _address(peer) if peer else None
        return address is not None and address in self.trusted_proxies

    def _request_host(self) -> str | None:
        return request_host(self.headers.get("Host"))

    def send_header(self, keyword, value):
        name = keyword.lower()
        if name == "content-security-policy":
            # Merged into the one policy end_headers sends, never a second header.
            self._route_csp = value if self._route_csp is None else f"{self._route_csp}; {value}"
            return
        if name in _EDGE_OWNED:
            return
        super().send_header(keyword, value)

    def end_headers(self):
        super().send_header("Strict-Transport-Security", HSTS)
        super().send_header("X-Frame-Options", X_FRAME_OPTIONS)
        super().send_header("Content-Security-Policy", with_frame_ancestors(self._route_csp))
        self._route_csp = None
        super().end_headers()
