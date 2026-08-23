from __future__ import annotations

from sentinel.detectors.base import Detection


def detect_permission_state_change(previous: dict | None, current: dict) -> list[Detection]:
    """Bitget exposes no endpoint for an independent account to list its own
    API keys (confirmed absent, docs/phase0-capability-audit.md section 1).
    This is the documented proxy for the 'new API key created' catalog item:
    watch OUR OWN key's authorities/ip-whitelist for drift. It cannot detect
    a new sibling key being created directly - only changes visible through
    our own key's account-info response - so it is always emitted at reduced
    confidence and explicitly labeled as a proxy so alert recipients don't
    mistake it for direct key-inventory visibility. (Topology A sub-accounts
    have a real direct signal instead - see the Phase 0 audit - this proxy
    is the topology-B fallback.)"""
    if previous is None:
        return []
    if previous.get("authorities") == current.get("authorities") and previous.get(
        "ip_whitelist"
    ) == current.get("ip_whitelist"):
        return []
    return [
        Detection(
            rule="account_permission_state_changed",
            severity="HIGH",
            confidence="low (proxy - Bitget exposes no API-key inventory for independent accounts)",
            message=(
                _describe_change(previous, current)
                + " This cannot confirm a new API key was created - Bitget doesn't expose "
                "that for independent accounts - but it is the closest available signal "
                "and warrants manual review."
            ),
            evidence={"previous": previous, "current": current},
        )
    ]


def _as_tokens(value) -> tuple[set[str], bool]:
    """The value as a comparable set of tokens, and whether it parsed. The
    exchange has answered with lists, comma-joined strings, and nothing at
    all; a shape this cannot read is reported as unparsed rather than
    silently treated as empty - 'empty' is a claim about permissions."""
    if value is None:
        return set(), False
    if isinstance(value, (list, tuple, set)):
        return {str(v) for v in value if str(v).strip()}, True
    if isinstance(value, str):
        return {t for t in value.replace(",", " ").split() if t}, True
    return set(), False


def _describe_change(previous: dict, current: dict) -> str:
    """#13's last criterion: the alert states exactly what changed, in plain
    words - 'the key gained the withdraw authority' is actionable at 3am in
    a way 'permission state changed' never was. Gains lead, because a key
    gaining power is the attack this detector exists to catch."""
    parts: list[str] = []
    for field, noun in (("authorities", "authority"), ("ip_whitelist", "IP-allowlist entry")):
        before_raw, after_raw = previous.get(field), current.get(field)
        if before_raw == after_raw:
            continue
        before, before_ok = _as_tokens(before_raw)
        after, after_ok = _as_tokens(after_raw)
        if not (before_ok and after_ok):
            parts.append(
                f"the {field.replace('_', ' ')} changed form and could not be itemised "
                f"(before: {before_raw!r}, after: {after_raw!r})")
            continue
        gained = sorted(after - before)
        lost = sorted(before - after)
        for token in gained:
            parts.append(f"the key GAINED the '{token}' {noun}")
        for token in lost:
            parts.append(f"the key lost the '{token}' {noun}")
        if not gained and not lost:
            # Same tokens, different raw shape (reordered list, respaced
            # string). A real difference in the raw values with no token
            # difference is still worth one honest sentence.
            parts.append(f"the {field.replace('_', ' ')} was rewritten without "
                         f"changing its entries")
    if not parts:
        parts.append("the permission state changed in a form this check could not describe")
    described = "; ".join(parts)
    return described[0].upper() + described[1:] + "."
