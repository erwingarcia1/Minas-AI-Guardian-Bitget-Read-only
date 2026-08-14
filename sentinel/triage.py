from __future__ import annotations

import json
import logging
import os

from sentinel.config import RulesConfig
from sentinel.db import Database

logger = logging.getLogger("sentinel.triage")

_SYSTEM = (
    "You are the triage assistant inside Sentinel, a Bitget account-compromise "
    "monitoring service. You receive the recent deterministic detection events "
    "for one account. Your job is ONLY to correlate them into a short incident "
    "narrative for a human responder: what likely happened, in what order, and "
    "what to check first. You may suggest that severity be revisited, but you "
    "have no authority to change it - the rules engine is authoritative, and "
    "CRITICAL alerts have already been sent regardless of what you say. "
    "Never advise ignoring or suppressing an alert. Be concrete and brief "
    "(under 200 words)."
)


class TriageEngine:
    """Optional, additive LLM layer. Correlates recent events into one
    incident narrative appended to the human-facing record. Deterministic
    rules remain authoritative by construction: triage runs AFTER alerts
    are dispatched and has no code path that can suppress, downgrade, or
    delay them."""

    def __init__(self, rules: RulesConfig):
        self._rules = rules
        self._client = None
        if not self.enabled:
            return
        try:
            import anthropic

            self._client = anthropic.Anthropic()
        except ImportError:
            logger.warning("triage enabled in config but the 'anthropic' package is not installed")
        except Exception as exc:  # noqa: BLE001 - missing key etc.; triage is optional
            # Stays a log line, decided in the #58 sweep. Triage writes a
            # narrative for a human who is already reading an alert; its
            # absence costs convenience, not detection. Nothing is missed and
            # no customer is uninformed, which is the bar the rest of that
            # sweep is measured against.
            logger.warning("triage disabled: %s", exc)

    @property
    def enabled(self) -> bool:
        return bool(self._rules.get("triage", "enabled", default=False)) and bool(
            os.environ.get("ANTHROPIC_API_KEY")
        )

    def narrate_incident(self, db: Database, account_id: int) -> str | None:
        """Returns an incident narrative for the account's recent events, or
        None when triage is off/unavailable. Failures never propagate - a
        broken LLM call must not affect the monitoring pipeline."""
        if self._client is None:
            return None

        max_events = self._rules.get_int("triage", "max_events", default=50)
        with db.cursor() as cur:
            cur.execute(
                "SELECT event_type, severity, payload_json, created_at FROM events "
                "WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
                (account_id, max_events),
            )
            rows = cur.fetchall()
        if not rows:
            return None

        events = [
            {
                "event_type": r["event_type"],
                "severity": r["severity"],
                "at": r["created_at"],
                "payload": json.loads(r["payload_json"]),
            }
            for r in rows
        ]

        import anthropic

        model = str(self._rules.get("triage", "model", default="claude-opus-4-8"))
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=4096,  # narratives are bounded to ~200 words; hard cost cap
                thinking={"type": "adaptive"},
                system=_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Recent detection events (newest first) for one monitored account:\n"
                            + json.dumps(events, default=str)
                            + "\n\nWrite the incident narrative."
                        ),
                    }
                ],
            )
        except anthropic.RateLimitError:
            logger.warning("triage rate-limited; skipping narrative")
            return None
        except anthropic.APIStatusError as exc:
            logger.warning("triage API error %s; skipping narrative", exc.status_code)
            return None
        except anthropic.APIConnectionError:
            logger.warning("triage connection error; skipping narrative")
            return None

        if response.stop_reason == "refusal":
            return None
        return next((b.text for b in response.content if b.type == "text"), None)
