"""Minas's own address, and whether a key is locked to it (decision 17's
stage 1, 2026-10-28).

Every exchange call leaves from one address, and docs/deployment.md has
always said each key must be restricted to it - but nothing checked, and the
steps a customer reads never mentioned it. A key left open to every address
works for whoever obtains it, from anywhere; Bybit lets an unrestricted key
lapse after 90 days, and OKX's agreement asks for allowlisting where it is
available (both researched 2026-10-28, cited in decision 17's review).

The address is a setting, `SENTINEL_EGRESS_IPS`, like the public name is
`SENTINEL_PORTAL_URL`: it is typed in exactly one place and read from there
by the key instructions and by the onboarding check. Unset, the steps say
"the address Minas gives you" and the check says it could not be made.

The check WARNS; it never refuses. How each exchange reports a key's
allowlist was read from the clients (sentinel/exchange.py) and their
fixtures, not from a live key on every exchange - UNVERIFIED for Bybit's
and OKX's unrestricted shapes - and a wrong refusal would block an
onboarding the admin is standing in front of. A wrong warning costs a
second look.
"""
from __future__ import annotations

import re

from sentinel.config import env_setting

EGRESS_SETTING = "SENTINEL_EGRESS_IPS"

# What a key's allowlist can be, as one word.
LOCKED = "locked"            # only Minas's addresses
ALSO_ELSEWHERE = "also_elsewhere"  # Minas's, and others too
ELSEWHERE = "elsewhere"      # addresses, none of them Minas's
OPEN = "open"                # no restriction at all
UNCHECKED = "unchecked"      # Minas's address is not configured, or the exchange does not say


def egress_ips() -> tuple[str, ...]:
    """The configured addresses, in the order given, without duplicates."""
    raw = env_setting(EGRESS_SETTING) or ""
    return tuple(dict.fromkeys(part for part in re.split(r"[,\s]+", raw) if part))


def egress_phrase() -> str:
    """The address as copy names it: the configured one(s), or a stand-in
    that sends the reader to Minas rather than to a typed number."""
    ips = egress_ips()
    return " or ".join(ips) if ips else "the address Minas gives you"


def _addresses(value) -> list[str]:
    """A key's allowlist as reported: a list (Bybit), or a comma-joined
    string (Bitget, OKX). '*' and blanks mean no restriction."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value))
    return [str(item).strip() for item in items if str(item).strip() and str(item).strip() != "*"]


def key_ip_lock(exchange: str, authorities, ips) -> tuple[str, str]:
    """(state, the sentence for the admin) for a key, from what its exchange
    reported at onboarding: `authorities` and `ips` as stored in the first
    permission snapshot."""
    ours = set(egress_ips())
    where = egress_phrase()
    if exchange == "binance":
        # Binance says only whether a key is restricted, not to what.
        flags = authorities if isinstance(authorities, (list, tuple)) else []
        if "ipRestrict" in flags:
            return UNCHECKED, ("Binance reports that this key is restricted to trusted IP addresses, but not "
                               f"which: check in Binance that {where} is the only one.")
        return OPEN, ("This key is not restricted to an IP address: anyone who obtains it can use it from "
                      f"anywhere. Ask the member to restrict it to {where}, then replace the key.")
    listed = _addresses(ips)
    if not listed:
        lapse = " Bybit lets an unrestricted key lapse after 90 days." if exchange == "bybit" else ""
        return OPEN, ("This key is not restricted to an IP address: anyone who obtains it can use it from "
                      f"anywhere.{lapse} Ask the member to restrict it to {where}, then replace the key.")
    if not ours:
        return UNCHECKED, (f"Minas's own address is not configured ({EGRESS_SETTING}), so this could not be "
                           f"checked. The key is restricted to: {', '.join(listed)}.")
    others = [ip for ip in listed if ip not in ours]
    if len(others) == len(listed):
        # Only reachable when the setting is wrong: a key restricted to other
        # addresses fails its read-only check from ours before it gets here.
        return ELSEWHERE, (f"This key is restricted to {', '.join(listed)}, none of which is the address "
                           f"configured as Minas's ({where}). If the key works, {EGRESS_SETTING} is wrong.")
    if others:
        return ALSO_ELSEWHERE, (f"This key works from Minas's address and also from {', '.join(others)}. "
                                f"Ask the member to restrict it to {where} only.")
    return LOCKED, f"This key is restricted to Minas's address ({where})."
