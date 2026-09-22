"""#42's two operator alarms: the checks fell behind schedule, and one
account has been unwatchable for too long.

Both are pages to MINAS, never to a customer - the thing that is broken is
ours (#53's whole point). The per-account heartbeat detector tells the
customer their account is dark; these tell the operator, and they are
deliberately shaped so a single wedged engine does not become one page per
account:

  - "checks behind schedule" looks at the NEWEST successful check across
    every active account. If even the freshest is older than the window,
    the engine as a whole is behind - one page says so.
  - "unwatchable too long" fires per account only when the engine is
    demonstrably working for the others - that is what makes it a fact
    about the account (dead key, broken exchange route) rather than a
    symptom of the global case above.

Honest limit, recorded where the code lives: these run on the scheduler
thread, so a fully stopped process cannot raise them. That is #63's
territory (external dead-man's switch, decision 7); these cover every
partial failure a live process can see.

Disabled accounts are ignored: an operator chose that stillness, the
portal and the digest already show it, and paging Minas about a pause
Minas made is noise.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sentinel.db import Database
from sentinel.watching import FAILURE_COUNTERS, counter, counter_columns_sql

logger = logging.getLogger("sentinel.alerts.watch_alarms")


class WatchAlarms:
    def __init__(self, db: Database, rules, operator_alerter) -> None:
        self._db = db
        self._rules = rules
        self._alerter = operator_alerter

    def _threshold(self, key: str, default: float) -> float:
        try:
            return float(self._rules.get_seconds("operator_alerting", key, default=default))
        except Exception:  # noqa: BLE001 - a bad threshold must not stop the scheduler
            return default

    def evaluate(self, now: datetime | None = None) -> None:
        if self._alerter is None:
            return
        now = now or datetime.now(timezone.utc)
        behind_after = self._threshold("checks_behind_seconds", 600)
        unwatchable_after = self._threshold("unwatchable_seconds", 1800)

        with self._db.cursor() as cur:
            cur.execute(
                f"SELECT a.id, a.label, a.onboarded_at, p.last_success_at, {counter_columns_sql('p')} "
                "FROM accounts a LEFT JOIN poll_state p ON p.account_id = a.id "
                "WHERE a.status = 'active'")
            rows = cur.fetchall()
        if not rows:
            return

        def _age(value: str | None, fallback: str | None) -> float | None:
            for candidate in (value, fallback):
                if candidate:
                    try:
                        return (now - datetime.fromisoformat(candidate)).total_seconds()
                    except ValueError:
                        continue
            return None

        # "Never checked" ages from onboarding: a two-minute-old account is
        # not behind, and one onboarded an hour ago with no success is.
        ages = [(_age(r["last_success_at"], r["onboarded_at"]), r) for r in rows]
        known = [a for a, _ in ages if a is not None]
        if not known:
            return

        freshest = min(known)
        if freshest >= behind_after:
            self._alerter.page(
                rule="checks_behind_schedule",
                severity="HIGH",
                message=(
                    f"No account has completed a check in {int(freshest // 60)} minutes "
                    f"(window: {int(behind_after // 60)}m). The polling engine as a whole "
                    f"is behind - this is one page for all {len(rows)} active account(s), "
                    f"not a per-account fault."
                ),
                evidence={"freshest_success_age_seconds": int(freshest),
                          "active_accounts": len(rows)},
                dedup_scope="checks-behind",
            )
            # One page covers everyone; per-account alarms while the whole
            # engine is behind would be the same fact times the fleet.
            return

        # A fleet-wide coverage failure is ONE event (alerts/fleet.py): page
        # once naming every member, and keep the per-account alarm quiet for
        # them - nine pages an hour about one changed address is noise.
        from sentinel.alerts.fleet import fleet_incident, page_fleet_incident

        incident = fleet_incident(self._db, self._rules, now)
        fleet_ids: set[int] = set()
        if incident:
            page_fleet_incident(self._db, self._alerter, incident)
            fleet_ids = set(incident["account_ids"])
        for age, row in ages:
            if age is None or age < unwatchable_after:
                continue
            if int(row["id"]) in fleet_ids:
                continue
            self._alerter.page(
                rule="account_unwatchable_too_long",
                severity="HIGH",
                message=(
                    f"{row['label']} has not completed a check in {int(age // 60)} minutes "
                    f"(window: {int(unwatchable_after // 60)}m) while other accounts are "
                    f"checking normally - the fault is specific to this account. "
                    f"Failures: {counter(row, 'consecutive_failures')} consecutive, "
                    f"{counter(row, 'consecutive_auth_failures')} auth."
                ),
                evidence={"account_label": row["label"],
                          "age_seconds": int(age),
                          **{name: counter(row, name) for name in FAILURE_COUNTERS}},
                account_id=int(row["id"]),
                dedup_scope=f"unwatchable:{row['label']}",
            )
