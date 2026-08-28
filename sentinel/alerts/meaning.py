"""What an alert MEANS, as distinct from how urgent it is (#103 item 3).

Severity answers "how urgent". Some rules also carry a meaning that decides
their routing on its own: `account_unreachable` and
`monitoring_key_tamper_suspected` are the product announcing it has gone
BLIND on a customer. That must reach a human directly - the customer and
Minas - on the same footing as a CRITICAL, whatever severity the detector
chose, and it must never depend on a scheduled job existing: 41 unreachable
alerts once routed to a digest that had never run.

The severity is NOT changed to inherit the routing. Changing it would
corrupt severity as a concept (the customer would be told "critical" about
an expired key). The routing is decided here, by meaning; the level stays
what the detector said.
"""
from __future__ import annotations

# Rules that mean "we are not watching this account". Add a rule here only
# if its firing means monitoring has stopped - not merely that something
# suspicious happened.
COVERAGE_RULES = frozenset({"account_unreachable", "monitoring_key_tamper_suspected"})

# The routing tier a coverage alert borrows: the one configured to reach a
# human immediately on every configured channel.
DIRECT_TIER = "CRITICAL"


def is_coverage_rule(rule: str | None) -> bool:
    return (rule or "") in COVERAGE_RULES


def routing_tier(rule: str | None, severity: str) -> str:
    """Which severity's channel set an alert is delivered on: its own, or
    the direct tier when its meaning demands a human now."""
    return DIRECT_TIER if is_coverage_rule(rule) else severity


def preference_tier(rule: str | None, severity: str) -> str:
    """Which severity's member preferences apply: a coverage alert is judged
    as a CRITICAL - never held for quiet hours, email is the floor."""
    return DIRECT_TIER if is_coverage_rule(rule) else severity
