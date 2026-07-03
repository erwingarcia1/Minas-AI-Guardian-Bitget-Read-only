from __future__ import annotations

from sentinel.detectors.base import Detection


def detect_unreachable(consecutive_failures: int, threshold: int) -> list[Detection]:
    """Account unreachable for N consecutive polls (any failure type,
    including transient network errors - distinct from the tamper signal,
    which requires auth/permission errors specifically)."""
    if consecutive_failures >= threshold:
        return [
            Detection(
                rule="account_unreachable",
                severity="MEDIUM",
                confidence="high",
                message=(
                    f"Account unreachable for {consecutive_failures} consecutive polls - "
                    f"monitoring coverage is degraded until this resolves"
                ),
                evidence={"consecutive_failures": consecutive_failures, "threshold": threshold},
            )
        ]
    return []


def detect_baseline_drift(new_deposit_addresses: list[str]) -> list[Detection]:
    """LOW-severity human-review signal: new but low-risk behavior. A new
    deposit sender address is normal account life, but a reviewer looking at
    a later incident wants these on the timeline."""
    if not new_deposit_addresses:
        return []
    return [
        Detection(
            rule="baseline_drift_new_deposit_source",
            severity="LOW",
            confidence="low",
            message=f"{len(new_deposit_addresses)} new deposit source address(es) added to baseline",
            evidence={"addresses": new_deposit_addresses},
        )
    ]
