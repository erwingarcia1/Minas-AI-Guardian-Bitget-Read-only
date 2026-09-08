from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Detection:
    rule: str
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    confidence: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    # #37: a drill is a simulated event. Labelled as such on every surface,
    # never escalated, never subject to a member's preferences (they asked).
    is_drill: bool = False
    bypass_preferences: bool = False
    # A routine receipt (2026-09-08): a withdrawal to a destination this
    # account has withdrawn to before. The customer reads it in their digest
    # and is never pushed for it; Minas sees it in the ops digest.
    receipt: bool = False
