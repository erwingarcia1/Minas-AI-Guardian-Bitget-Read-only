"""What a valid config/rules.yaml looks like.

Every setting the code actually reads is declared here, with a constraint
derived from *how the value is used* rather than from an opinion about how
sensitive a detector ought to be. That distinction is the whole design rule of
this file, and it matters: quieting a noisy rule without a deploy is the user
story (G-12), so validation that second-guesses an operator's tuning would
break the feature it is meant to protect.

So `max_deviation_pct: "75"` passes — wide, but it means something. `"-1"` does
not, because `deviation > -1` is true of every fill ever executed and there is
no tuning intent that expression could express. Each constraint below carries
the expression it comes from.

Unknown keys are rejected too. A misspelled key is the quietest failure of the
lot: it parses as valid YAML, the reader falls through to its default, and the
operator goes away believing the new value took effect.
"""

from __future__ import annotations

import difflib
from decimal import Decimal

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


class _Rule:
    def __init__(self, check, describe):
        self.check = check
        self.describe = describe


def _is_number(value) -> bool:
    # bool is an int subclass in Python, and `true` is never a threshold.
    return not isinstance(value, bool) and isinstance(value, (int, float, Decimal))


def _number(predicate, description: str) -> _Rule:
    def check(value):
        if not _is_number(value):
            return "must be a number"
        return None if predicate(value) else description

    return _Rule(check, description)


POSITIVE = _number(lambda v: v > 0, "must be greater than zero")
NON_NEGATIVE = _number(lambda v: v >= 0, "must be zero or greater")
# alpha*observation + (1-alpha)*previous is only an average inside (0, 1].
EMA_ALPHA = _number(lambda v: 0 < v <= 1, "must be greater than 0 and at most 1")


def _boolean() -> _Rule:
    return _Rule(lambda v: None if isinstance(v, bool) else "must be true or false", "must be true or false")


def _text() -> _Rule:
    return _Rule(lambda v: None if isinstance(v, str) else "must be text", "must be text")


def _channel_list() -> _Rule:
    def check(value):
        if not isinstance(value, list):
            return "must be a list of channel names"
        if not all(isinstance(v, str) for v in value):
            return "must contain only channel names"
        return None

    return _Rule(check, "must be a list of channel names")


# Keyed exactly as the YAML nests. A dict value is a section; a _Rule is a leaf.
SPEC: dict = {
    "polling": {
        # Loop intervals: a zero or negative sleep is a busy loop against the
        # exchange, not a faster poll.
        "fast_loop_seconds": POSITIVE,
        "slow_loop_seconds": POSITIVE,
        "jitter_seconds": NON_NEGATIVE,  # zero jitter is legitimate (deterministic scheduling)
    },
    "workers": {
        "pool_size": POSITIVE,  # a pool of zero consumes no tasks: nothing is ever polled
    },
    "rate_limits": {
        # Token buckets. A non-positive rate or burst never grants a token, so
        # every poll blocks forever.
        "per_account_rps": POSITIVE,
        "per_account_burst": POSITIVE,
        "egress_ip_rps": POSITIVE,
        "egress_ip_burst": POSITIVE,
    },
    "circuit_breaker": {
        "failure_threshold": POSITIVE,  # opening after zero failures opens permanently
        "base_backoff_seconds": POSITIVE,
        "max_backoff_seconds": POSITIVE,
    },
    "detectors": {
        "quantity_drop": {
            # Compared as `shortfall > tolerance`. Negative is exceeded by any
            # balance at all, including one that went up.
            "tolerance_pct": NON_NEGATIVE,
            "tolerance_absolute": NON_NEGATIVE,
            # Widens the explanation window backwards. Negative would push it
            # forwards, silently narrowing what may explain a drop.
            "explanation_grace_seconds": NON_NEGATIVE,
        },
        "tamper": {
            # `consecutive_failures >= threshold`. At zero the account is in
            # tamper state before it has failed once.
            "consecutive_auth_failures_threshold": POSITIVE,
        },
        "heartbeat": {
            "consecutive_failures_threshold": POSITIVE,
        },
        "withdrawal_burst": {
            "window_minutes": POSITIVE,  # a zero-length window contains nothing
            "min_count": POSITIVE,  # a burst of fewer than one withdrawal is not a burst
        },
        "off_market": {
            # `deviation_pct > max_deviation_pct`, and deviation is an absolute
            # value — so a negative threshold flags every fill on the account.
            "max_deviation_pct": NON_NEGATIVE,
        },
        "fee_anomaly": {
            # `spent > baseline * multiplier`. At zero or below, every trade
            # carrying a fee above the floor is an anomaly.
            "multiplier": POSITIVE,
            "min_fee_floor": NON_NEGATIVE,  # zero floor = suppress nothing, a real choice
            "ema_alpha": EMA_ALPHA,
        },
        "transfers": {
            "max_coins_per_cycle": POSITIVE,  # zero coins swept means transfers are never fetched
        },
    },
    "alerting": {
        "routing": {sev: _channel_list() for sev in SEVERITIES},
        # Zero cooldown means never suppress a repeat — noisy, but a choice an
        # operator is entitled to make.
        "cooldown_minutes": {sev: NON_NEGATIVE for sev in SEVERITIES},
        "escalation_unacknowledged_minutes": POSITIVE,
        "sms_cooldown_minutes": NON_NEGATIVE,
        "sms_critical_overrides_optout": _boolean(),
    },
    # Paging scoped to Minas rather than to a customer (G-49). Separate from
    # `alerting` on purpose: "our product is broken" and "this customer may be
    # being robbed" are different kinds of urgency and need their own dials.
    "operator_alerting": {
        "routing": {sev: _channel_list() for sev in SEVERITIES},
        "cooldown_minutes": {sev: NON_NEGATIVE for sev in SEVERITIES},
        # G-48's alarm. The counter's resting value is zero, so there is no
        # sensitivity threshold here - only how long a rise must persist before
        # it is a format change rather than a blip.
        "untimestamped_window_seconds": POSITIVE,
        "untimestamped_consecutive_windows": POSITIVE,
    },
    "triage": {
        "enabled": _boolean(),
        "model": _text(),
        "max_events": POSITIVE,
    },
}


def _suggest(key: str, candidates) -> str:
    """Points at the key the operator probably meant. A misspelling is the
    common case here, so naming the near-match turns a rejection into a fix."""
    close = difflib.get_close_matches(key, list(candidates), n=1, cutoff=0.6)
    return f" — did you mean '{close[0]}'?" if close else ""


def prune_invalid(data, spec=SPEC):
    """A copy of `data` with every setting the schema rejects removed.

    What survives is exactly the settings that validate, so the readers in
    `RulesConfig` fall through to their own defaults for the rest. That
    granularity is the point: all-or-nothing would discard an operator's entire
    detector tuning because they mistyped `triage.max_events`, which is its own
    kind of surprise.

    Used when the process must run on a file it can't fully accept - see
    `RulesConfig._reject`. Never used to decide *whether* a file is valid;
    `validate` does that, and it is what `sentinel validate-config` reports.
    """
    if not isinstance(data, dict):
        return {}
    clean: dict = {}
    for key, value in data.items():
        if key not in spec:
            continue
        expected = spec[key]
        if isinstance(expected, dict):
            section = prune_invalid(value, expected)
            if section:
                clean[key] = section
        elif expected.check(value) is None:
            clean[key] = value
    return clean


def validate(data, spec=SPEC, path=()) -> list[str]:
    """Returns a list of human-readable problems; empty means valid.

    Every problem names its full dotted path and the offending value, because
    the message is read by someone who has just broken production monitoring
    and wants to know which line to fix.
    """
    problems: list[str] = []
    if data is None:
        return problems
    if not isinstance(data, dict):
        return [f"{'.'.join(path) or 'file'}: expected a section, found {data!r}"]

    for key, value in data.items():
        where = ".".join(path + (str(key),))
        if key not in spec:
            problems.append(f"{where}: unknown setting{_suggest(str(key), spec)}")
            continue
        expected = spec[key]
        if isinstance(expected, dict):
            problems.extend(validate(value, expected, path + (str(key),)))
        else:
            failure = expected.check(value)
            if failure:
                problems.append(f"{where}: {failure} (found {value!r})")
    return problems
