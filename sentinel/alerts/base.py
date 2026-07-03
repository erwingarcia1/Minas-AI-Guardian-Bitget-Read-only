from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

RUNBOOK = (
    "Immediate actions: 1) Freeze the account in the Bitget app if you did not "
    "authorize this. 2) Revoke/rotate the API key(s) on this account. 3) Verify "
    "your withdrawal address whitelist in the Bitget app. 4) Enable/verify 2FA "
    "and Bitget's anti-phishing code. 5) Contact Bitget support if funds moved "
    "without your authorization."
)


@dataclass
class AlertMessage:
    account_label: str
    rule: str
    severity: str
    confidence: str
    message: str
    evidence: dict

    def render(self) -> str:
        evidence_lines = "\n".join(f"  {k}: {v}" for k, v in self.evidence.items())
        return (
            f"🛡 MINAS AI GUARDIAN\n"
            f"[{self.severity}] {self.rule} - {self.account_label}\n"
            f"{self.message}\n"
            f"Confidence: {self.confidence}\n"
            f"Evidence:\n{evidence_lines}\n\n"
            f"Runbook:\n{RUNBOOK}"
        )


class AlertChannel(ABC):
    @abstractmethod
    def send(self, alert: AlertMessage) -> None: ...
