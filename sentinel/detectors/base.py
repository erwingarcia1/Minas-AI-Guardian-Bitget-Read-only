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
