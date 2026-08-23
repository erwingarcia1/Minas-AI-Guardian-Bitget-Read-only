"""Per-exchange, text-only instructions for creating a read-only key (#8,
decision 14).

No screenshots, by the owner's decision: written steps that stay accurate
beat pictures that quietly stop matching what the customer is looking at.
The one requirement that makes text honest: every set is DATED and NAMES
the interface it was written against, and that line is customer-visible -
staleness is disclosed, not discovered. The revisit trigger is onboarding
support load, not a calendar: when "the steps don't match my screen"
appears in support contact, the affected exchange's entry is rewritten and
re-dated here.

Steps deliberately describe what to choose, not pixel positions - the
names of permissions are the durable part of an exchange UI, and they are
also exactly what our verifier checks, so the instructions and the
rejection reasons speak the same words.
"""
from __future__ import annotations

# exchange -> the instruction set. `fix` is the one-line answer to "my key
# was rejected - which permission do I change": it names the same tokens
# verify_read_only refuses on.
KEY_INSTRUCTIONS: dict[str, dict] = {
    "bitget": {
        "written": "2026-08",
        "interface": "Bitget web app — API Management page",
        "steps": [
            "In the Bitget web app, open your profile menu and choose API Management.",
            "Choose Create API Key, then System-generated API Key.",
            "Set a passphrase and keep it — Bitget keys need it, and we will ask for it.",
            "Under permissions, tick Read-only only. Leave every trade and "
            "withdrawal permission unticked.",
            "Complete Bitget's verification, then copy the API key, the secret, "
            "and your passphrase into the form here.",
        ],
        "fix": "Recreate the key with the Read-only permission only — any "
               "trade, transfer, or withdrawal authority makes us refuse it.",
    },
    "bybit": {
        "written": "2026-08",
        "interface": "Bybit web app — API management page",
        "steps": [
            "In the Bybit web app, open your profile menu and choose API.",
            "Choose Create New Key, then System-generated API Keys.",
            "Under permissions, choose Read-Only. Do not grant trade, transfer, "
            "or withdrawal permissions.",
            "No passphrase exists on Bybit — leave that field empty here.",
            "Complete Bybit's verification, then copy the API key and secret "
            "into the form here.",
        ],
        "fix": "Recreate the key as Read-Only — Bybit reports a read/write flag "
               "to us, and anything but read-only is refused.",
    },
    "okx": {
        "written": "2026-08",
        "interface": "OKX web app — API keys page",
        "steps": [
            "In the OKX web app, open your profile menu and choose API keys.",
            "Choose Create API key.",
            "Set a passphrase and keep it — OKX keys need it, and we will ask for it.",
            "Under permissions, tick Read only. Leave Trade and Withdraw unticked.",
            "Complete OKX's verification, then copy the API key, the secret, and "
            "your passphrase into the form here.",
        ],
        "fix": "Recreate the key with only the Read permission — an OKX key "
               "carrying trade or withdraw is refused.",
    },
    "binance": {
        "written": "2026-08",
        "interface": "Binance web app — API Management page",
        "steps": [
            "In the Binance web app, open your profile menu and choose Account, "
            "then API Management.",
            "Choose Create API, name the key, and complete verification.",
            "Leave the permissions at their default: Enable Reading ONLY. Do not "
            "enable spot trading, margin, futures, transfers, or withdrawals.",
            "No passphrase exists on Binance — leave that field empty here.",
            "Copy the API key and secret into the form here.",
        ],
        "fix": "Edit or recreate the key so only Enable Reading is on — every "
               "other toggle (trading, transfers, withdrawals) makes us refuse it.",
    },
}


def instructions_for(exchange: str) -> dict:
    """The instruction set for this exchange, or a fallback that says
    plainly there are none - never a silent empty page."""
    return KEY_INSTRUCTIONS.get((exchange or "").lower(), {
        "written": None,
        "interface": None,
        "steps": ["We have not written steps for this exchange yet — contact "
                  "the Minas team and we will walk you through it."],
        "fix": "Create the key with read/view permission only.",
    })


def provenance_line(entry: dict) -> str:
    """The customer-visible date-and-interface line decision 14 requires -
    the disclosure that makes text-only honest."""
    if not entry.get("written"):
        return ""
    return (f"Written against the {entry['interface']}, {entry['written']}. "
            "If these steps don't match what your exchange shows you, tell us — "
            "that is how they stay current.")
