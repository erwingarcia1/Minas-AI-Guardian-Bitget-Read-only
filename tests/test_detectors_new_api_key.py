from __future__ import annotations

from sentinel.detectors.new_api_key import detect_permission_state_change


def test_first_observation_does_not_trigger():
    detections = detect_permission_state_change(None, {"authorities": "a", "ip_whitelist": "1.2.3.4"})
    assert detections == []


def test_unchanged_state_does_not_trigger():
    prev = {"authorities": "a", "ip_whitelist": "1.2.3.4"}
    curr = {"authorities": "a", "ip_whitelist": "1.2.3.4"}
    assert detect_permission_state_change(prev, curr) == []


def test_changed_authorities_triggers_proxy_alert():
    prev = {"authorities": "spot_account_r", "ip_whitelist": "1.2.3.4"}
    curr = {"authorities": "spot_account_r,spot_trade_w", "ip_whitelist": "1.2.3.4"}
    detections = detect_permission_state_change(prev, curr)
    assert len(detections) == 1
    assert detections[0].rule == "account_permission_state_changed"
    assert "proxy" in detections[0].confidence


# --- #13's last criterion: the alert says exactly what changed ----------------

def test_a_gained_authority_is_named_and_leads(db=None):
    prev = {"authorities": ["read_only"], "ip_whitelist": []}
    curr = {"authorities": ["read_only", "withdraw"], "ip_whitelist": []}
    (d,) = detect_permission_state_change(prev, curr)
    assert "The key GAINED the 'withdraw' authority." in d.message
    assert "permission state changed in a form" not in d.message


def test_a_lost_authority_and_an_allowlist_gain_are_both_named():
    prev = {"authorities": ["read_only", "trade"], "ip_whitelist": ["10.0.0.1"]}
    curr = {"authorities": ["read_only"], "ip_whitelist": ["10.0.0.1", "203.0.113.9"]}
    (d,) = detect_permission_state_change(prev, curr)
    assert "he key lost the 'trade' authority" in d.message
    assert "the key GAINED the '203.0.113.9' IP-allowlist entry" in d.message


def test_comma_joined_strings_diff_the_same_as_lists():
    prev = {"authorities": "read_only,trade", "ip_whitelist": None}
    curr = {"authorities": "read_only", "ip_whitelist": None}
    (d,) = detect_permission_state_change(prev, curr)
    assert "the key lost the 'trade' authority" in d.message.lower()
    assert "could not be itemised" not in d.message


def test_a_reordered_list_is_reported_as_rewritten_not_as_gain_or_loss():
    prev = {"authorities": ["a", "b"], "ip_whitelist": []}
    curr = {"authorities": ["b", "a"], "ip_whitelist": []}
    (d,) = detect_permission_state_change(prev, curr)
    assert "rewritten without changing its entries" in d.message
    assert "GAINED" not in d.message


def test_an_unparseable_shape_is_admitted_not_silently_emptied():
    prev = {"authorities": {"weird": True}, "ip_whitelist": []}
    curr = {"authorities": ["read_only"], "ip_whitelist": []}
    (d,) = detect_permission_state_change(prev, curr)
    assert "could not be itemised" in d.message
    assert "before:" in d.message and "after:" in d.message


def test_the_proxy_disclaimer_survives_the_rewrite():
    prev = {"authorities": ["read_only"], "ip_whitelist": []}
    curr = {"authorities": ["withdraw"], "ip_whitelist": []}
    (d,) = detect_permission_state_change(prev, curr)
    assert "cannot confirm a new API key was created" in d.message


def test_an_allowlist_change_is_named_even_when_authorities_are_unchanged():
    """Survivor x__describe_change__mutmut_15: equal authorities must skip
    that field and keep going - not silence the allowlist diff behind it."""
    prev = {"authorities": ["read_only"], "ip_whitelist": ["10.0.0.1"]}
    curr = {"authorities": ["read_only"], "ip_whitelist": ["10.0.0.1", "203.0.113.9"]}
    (d,) = detect_permission_state_change(prev, curr)
    assert "GAINED the '203.0.113.9' IP-allowlist entry" in d.message
    assert "could not describe" not in d.message


def test_unparseable_authorities_do_not_silence_the_allowlist_diff():
    """Survivor x__describe_change__mutmut_29: the itemise-admission for one
    field must not stop the next field being described."""
    prev = {"authorities": {"weird": 1}, "ip_whitelist": ["10.0.0.1"]}
    curr = {"authorities": ["read_only"], "ip_whitelist": []}
    (d,) = detect_permission_state_change(prev, curr)
    assert "could not be itemised" in d.message
    assert "lost the '10.0.0.1' IP-allowlist entry" in d.message
