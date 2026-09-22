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


# How long a coverage condition stays "the same incident". A key that broke
# on Monday and is still broken on Friday is one incident; the customer is
# reminded daily by email (unreachable_repeat_minutes), Minas and the
# emergency contact heard about it LOUDLY once and are not paged again for
# it. Measured before this existed (v1.11.0 as tagged): one broken key cost
# the customer 2 emails a day, the emergency contact a note a day and Minas
# 9 messages a day, for as long as the key stayed broken - Paula's did for
# 31 days.
REPEAT_WINDOW_DAYS = 30


def is_coverage_rule(rule: str | None) -> bool:
    return (rule or "") in COVERAGE_RULES


REPEAT_CADENCE_KEY = ("alerting", "unreachable_repeat_minutes")


def coverage_repeat_cooldowns(rules) -> dict[str, int]:
    """The repeat cadence of the CLASS: every coverage rule re-fires on the
    daily reminder cadence (`alerting.unreachable_repeat_minutes`), never on
    its severity's cooldown. This was per rule until 2026-09-22: only
    account_unreachable was named, and its sibling monitoring_key_tamper_
    suspected fell back to the HIGH cooldown - so when nine keys were
    refused by address at once, nine customers were emailed every 62
    minutes, 145 repeats in sixteen hours. Built from COVERAGE_RULES so the
    next coverage rule cannot miss it; a test enumerates the set."""
    minutes = rules.get_int(*REPEAT_CADENCE_KEY)
    return {rule: minutes for rule in COVERAGE_RULES}


def routing_tier(rule: str | None, severity: str, *, repeat: bool = False) -> str | None:
    """Which severity's channel set an alert is delivered on: its own, or
    the direct tier when its meaning demands a human now - or NONE of the
    global channels when it is a coverage REPEAT: the customer still gets
    their daily email through the contact router, but Minas heard the
    first one loudly and does not need the same news every day."""
    if is_coverage_rule(rule):
        return None if repeat else DIRECT_TIER
    return severity


def preference_tier(rule: str | None, severity: str) -> str:
    """Which severity's member preferences apply: a coverage alert is judged
    as a CRITICAL - never held for quiet hours, email is the floor."""
    return DIRECT_TIER if is_coverage_rule(rule) else severity
