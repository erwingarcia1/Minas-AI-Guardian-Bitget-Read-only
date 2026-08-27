#!/usr/bin/env python3
"""Backfill sibling acknowledgements (#99, decision 18) - dry run by default.

    scripts/backfill-siblings.py --db PATH [--dry-run]           # prints what it WOULD change
    scripts/backfill-siblings.py --db PATH --apply --as OPERATOR # writes; the owner's act

#99 made acknowledging one alert acknowledge its siblings from then on. It
left the alerts already open untouched, because production is read-only to
the autonomous run. This is the backfill: for every alert that is still
unacknowledged, if an alert on the same account carrying the same non-empty
`order_id` was acknowledged within 24 hours of it (the SAME definition
sentinel/alerts/siblings.py enforces - nothing wider), acknowledge it too,
attributed to the backfill and naming the sibling whose acknowledgement it
inherits.

Nothing else moves: no closing notes are sent (the human already handled the
action), no escalation state changes, and the definition is not widened -
decision 18 is the owner's and still open.

Fail-closed defaults: without --apply this never writes and prints the exact
rows; --apply also needs --as, and refuses a path that looks like the live
volume unless --i-mean-production is given, because the owner runs it there,
not a session against a copy that turned out to be the real thing.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

WINDOW = timedelta(hours=24)


def candidates(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    open_alerts = conn.execute(
        "SELECT id, account_id, rule, severity, sent_at, evidence_json FROM alerts "
        "WHERE acknowledged_at IS NULL ORDER BY id").fetchall()
    out = []
    for a in open_alerts:
        try:
            order_id = json.loads(a["evidence_json"] or "{}").get("order_id")
        except (ValueError, TypeError):
            continue
        if order_id is None or str(order_id).strip() == "":
            continue
        try:
            anchor = datetime.fromisoformat(a["sent_at"])
        except ValueError:
            continue
        acked = conn.execute(
            "SELECT id, sent_at, evidence_json, acknowledged_at, acknowledged_by FROM alerts "
            "WHERE account_id = ? AND id != ? AND acknowledged_at IS NOT NULL",
            (a["account_id"], a["id"])).fetchall()
        for s in acked:
            try:
                s_order = json.loads(s["evidence_json"] or "{}").get("order_id")
                if s_order is None or str(s_order) != str(order_id):
                    continue
                if abs(datetime.fromisoformat(s["sent_at"]) - anchor) > WINDOW:
                    continue
            except (ValueError, TypeError):
                continue
            out.append({
                "alert_id": int(a["id"]), "account_id": int(a["account_id"]), "rule": a["rule"],
                "severity": a["severity"], "sent_at": a["sent_at"], "order_id": str(order_id),
                "sibling_id": int(s["id"]), "sibling_acknowledged_at": s["acknowledged_at"],
                "sibling_acknowledged_by": s["acknowledged_by"],
            })
            break
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--as", dest="actor")
    ap.add_argument("--i-mean-production", action="store_true")
    args = ap.parse_args(argv[1:])
    if args.apply and not args.actor:
        print("--apply needs --as <operator>: the backfill is recorded against a name", file=sys.stderr)
        return 2
    if args.apply and "/var/lib/docker/volumes" in args.db and not args.i_mean_production:
        print("that path is the live volume; pass --i-mean-production if you are the owner and mean it",
              file=sys.stderr)
        return 2
    mode = "ro" if not args.apply else "rw"
    conn = sqlite3.connect(f"file:{args.db}?mode={mode}", uri=True)
    rows = candidates(conn)
    total_open_critical = conn.execute(
        "SELECT count(*) FROM alerts WHERE severity = 'CRITICAL' AND acknowledged_at IS NULL").fetchone()[0]
    print(f"open CRITICAL alerts: {total_open_critical}")
    print(f"would acknowledge {len(rows)} alert(s) as siblings of already-acknowledged alerts:")
    for r in rows:
        print(f"  #{r['alert_id']:<6} account {r['account_id']:<4} {r['severity']:<8} {r['rule']:<32} "
              f"{r['sent_at'][:19]}  order {r['order_id']}  <- sibling #{r['sibling_id']} "
              f"acked {r['sibling_acknowledged_at'][:19]} by {r['sibling_acknowledged_by']}")
    if not args.apply:
        print("dry run: nothing written")
        return 0
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for r in rows:
            conn.execute(
                "UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ? "
                "WHERE id = ? AND acknowledged_at IS NULL",
                (now, f"{args.actor} (backfill: sibling of #{r['sibling_id']})", r["alert_id"]))
    print(f"applied: {len(rows)} alert(s) acknowledged by {args.actor} at {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
