from __future__ import annotations

import html
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sentinel.db import Database

_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minas AI Guardian</title>
<meta http-equiv="refresh" content="30">
<style>
  :root {{
    --bg: #0b0813;
    --panel: #140f20;
    --panel-2: #191229;
    --line: #2b2140;
    --line-soft: #211935;
    --txt: #ece8f4;
    --muted: #9a8fb5;
    --faint: #6b5f8a;
    --accent: #8b5cf6;
    --gold: #f0b429;
    --ok: #34d399;
    --crit: #ff5d6c;
    --high: #ffa63d;
    --med: #ffd23d;
    --low: #8a7fa5;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background:
      radial-gradient(1200px 600px at 85% -10%, rgba(139,92,246,.13), transparent 60%),
      radial-gradient(900px 500px at 0% 0%, rgba(240,180,41,.06), transparent 55%),
      var(--bg);
    color: var(--txt);
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
    letter-spacing: .1px;
    min-height: 100vh;
  }}
  .wrap {{ max-width: 1120px; margin: 0 auto; padding: 40px 32px 64px; }}

  header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 34px; }}
  .brand {{ display: flex; align-items: center; gap: 14px; }}
  .mark {{
    width: 40px; height: 40px; border-radius: 12px;
    background: linear-gradient(145deg, #1c1230, #0d0918);
    border: 1px solid rgba(139,92,246,.45);
    display: grid; place-items: center;
    box-shadow: 0 6px 22px rgba(139,92,246,.30), inset 0 1px 0 rgba(255,255,255,.08);
  }}
  .mark svg {{ width: 24px; height: 24px; }}
  .brand h1 {{ font-size: 19px; font-weight: 650; margin: 0; letter-spacing: .2px; }}
  .brand h1 .minas {{ color: var(--gold); font-weight: 780; letter-spacing: 3px; }}
  .brand .sub {{ font-size: 12px; color: var(--faint); margin-top: 2px; }}
  .nav {{ display: flex; align-items: center; gap: 10px; }}
  .live {{ display: inline-flex; align-items: center; gap: 9px; font-size: 12.5px; color: var(--muted);
           background: var(--panel); border: 1px solid var(--line); padding: 8px 14px;
           border-radius: 999px; text-decoration: none; }}
  a.live:hover {{ border-color: var(--accent); color: var(--txt); }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--ok);
          box-shadow: 0 0 0 0 rgba(52,211,153,.6); animation: pulse 2.2s infinite; }}
  @keyframes pulse {{
    0% {{ box-shadow: 0 0 0 0 rgba(52,211,153,.55); }}
    70% {{ box-shadow: 0 0 0 8px rgba(52,211,153,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(52,211,153,0); }}
  }}

  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 30px; }}
  .kpi {{
    background: linear-gradient(180deg, var(--panel-2), var(--panel));
    border: 1px solid var(--line); border-radius: 16px; padding: 18px 20px;
    position: relative; overflow: hidden;
  }}
  .kpi::after {{ content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
                 background: linear-gradient(90deg, transparent, rgba(139,92,246,.6), rgba(240,180,41,.5), transparent); opacity: .6; }}
  .kpi .label {{ font-size: 11.5px; text-transform: uppercase; letter-spacing: .8px; color: var(--faint); }}
  .kpi .val {{ font-size: 32px; font-weight: 680; margin-top: 8px; font-variant-numeric: tabular-nums; line-height: 1; }}
  .kpi .val.crit {{ color: var(--crit); }}
  .kpi .val.ok {{ color: var(--ok); }}
  .kpi .foot {{ font-size: 12px; color: var(--muted); margin-top: 8px; }}

  section {{ margin-top: 26px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; }}
  .card-head {{ display: flex; align-items: baseline; justify-content: space-between;
                padding: 16px 22px; border-bottom: 1px solid var(--line-soft); }}
  .card-head h2 {{ font-size: 14px; font-weight: 620; margin: 0; letter-spacing: .3px; }}
  .card-head .count {{ font-size: 12px; color: var(--faint); font-variant-numeric: tabular-nums; }}

  table {{ width: 100%; border-collapse: collapse; }}
  thead th {{ font-size: 11px; text-transform: uppercase; letter-spacing: .7px; color: var(--faint);
              text-align: left; padding: 12px 22px; font-weight: 600; }}
  tbody td {{ padding: 14px 22px; font-size: 13.5px; border-top: 1px solid var(--line-soft); }}
  tbody tr:hover {{ background: rgba(139,92,246,.05); }}
  .mono {{ font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, monospace;
           font-size: 12.5px; color: var(--muted); font-variant-numeric: tabular-nums; }}
  .rule {{ font-family: "SF Mono", ui-monospace, Menlo, monospace; font-size: 12.5px; }}
  .lbl {{ font-weight: 550; }}

  .pill {{ display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; font-weight: 600;
           padding: 4px 11px; border-radius: 999px; letter-spacing: .3px; }}
  .pill::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
  .s-active {{ color: var(--ok); background: rgba(52,211,153,.12); }}
  .s-disabled {{ color: var(--faint); background: rgba(91,104,131,.15); }}
  .s-tamper {{ color: var(--crit); background: rgba(255,93,108,.12); }}
  .sev-CRITICAL {{ color: var(--crit); background: rgba(255,93,108,.13); }}
  .sev-HIGH {{ color: var(--high); background: rgba(255,166,61,.13); }}
  .sev-MEDIUM {{ color: var(--med); background: rgba(255,210,61,.13); }}
  .sev-LOW {{ color: var(--low); background: rgba(124,138,165,.15); }}
  .ack-yes {{ color: var(--ok); }} .ack-no {{ color: var(--faint); }}
  .fail {{ color: var(--high); font-weight: 600; }} .zero {{ color: var(--faint); }}
  .empty {{ padding: 34px; text-align: center; color: var(--faint); font-size: 13px; }}

  footer {{ margin-top: 28px; text-align: center; color: var(--faint); font-size: 11.5px; }}
  @media (max-width: 720px) {{ .kpis {{ grid-template-columns: repeat(2, 1fr); }} .wrap {{ padding: 24px 16px; }} }}
</style></head><body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="mark"><svg viewBox="0 0 24 24" fill="none"><defs><linearGradient id="inf" x1="3" y1="20" x2="21" y2="4" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#f0b429"/><stop offset=".48" stop-color="#f0b429"/><stop offset=".58" stop-color="#8b5cf6"/><stop offset="1" stop-color="#6d28d9"/></linearGradient></defs><path fill="url(#inf)" d="M18.6 6.62c-1.44 0-2.8.56-3.77 1.53L12 10.66 10.48 12h.01L7.8 14.39c-.64.64-1.49.99-2.4.99-1.87 0-3.39-1.51-3.39-3.38S3.53 8.62 5.4 8.62c.91 0 1.76.35 2.44 1.03l1.13 1 1.51-1.34L9.22 8.2C8.2 7.18 6.84 6.62 5.4 6.62 2.42 6.62 0 9.04 0 12s2.42 5.38 5.4 5.38c1.44 0 2.8-.56 3.77-1.53l2.83-2.5.01.01L13.52 12h-.01l2.69-2.39c.64-.64 1.49-.99 2.4-.99 1.87 0 3.39 1.51 3.39 3.38s-1.52 3.38-3.39 3.38c-.9 0-1.76-.35-2.44-1.03l-1.14-1.01-1.51 1.34 1.27 1.12c1.02 1.01 2.37 1.57 3.82 1.57 2.98 0 5.4-2.41 5.4-5.38s-2.42-5.38-5.4-5.38z"/></svg></div>
      <div><h1><span class="minas">MINAS</span> AI Guardian</h1><div class="sub">Bitget account-compromise monitoring</div></div>
    </div>
    <div class="nav">
      <a class="live" id="metrics-link" href="/metrics">Metrics</a>
      <span class="live"><span class="dot"></span> Live · refreshed {now}</span>
    </div>
  </header>

  <div class="kpis">
    <div class="kpi"><div class="label">Accounts monitored</div><div class="val">{total_accounts}</div><div class="foot">{active_accounts} active · {disabled_accounts} disabled</div></div>
    <div class="kpi"><div class="label">Fleet health</div><div class="val ok">{health_pct}%</div><div class="foot">{healthy} of {active_accounts} reporting</div></div>
    <div class="kpi"><div class="label">Unacknowledged critical</div><div class="val {crit_cls}">{unacked_crit}</div><div class="foot">{crit_24h} critical in last 24h</div></div>
    <div class="kpi"><div class="label">Alerts · 24h</div><div class="val">{alerts_24h}</div><div class="foot">{high_24h} high · {medlow_24h} med/low</div></div>
  </div>

  <section>
    <div class="card">
      <div class="card-head"><h2>Accounts</h2><span class="count">{total_accounts} total</span></div>
      <table>
        <thead><tr><th>Account</th><th>Topology</th><th>Status</th><th>Last success</th><th>Failures</th><th>Auth</th></tr></thead>
        <tbody>{accounts}</tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="card">
      <div class="card-head"><h2>Recent alerts</h2><span class="count">latest 50</span></div>
      <table>
        <thead><tr><th>Time (UTC)</th><th>Account</th><th>Severity</th><th>Rule</th><th>Ack</th></tr></thead>
        <tbody>{alerts}</tbody>
      </table>
    </div>
  </section>

  <footer>Minas AI Guardian · internal ops view · network-gated · auto-refreshes every 30s</footer>
</div>
</body></html>"""


def _status_pill(status: str) -> str:
    cls = {"active": "s-active", "disabled": "s-disabled", "tamper_suspected": "s-tamper"}.get(status, "s-disabled")
    label = status.replace("_", " ")
    return f'<span class="pill {cls}">{html.escape(label)}</span>'


def _fail_cell(n: int) -> str:
    return f'<span class="fail">{n}</span>' if n else '<span class="zero">0</span>'


def _render(db: Database) -> str:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()

    with db.cursor() as cur:
        cur.execute(
            "SELECT a.label, a.topology, a.status, p.last_success_at, p.consecutive_failures, "
            "p.consecutive_auth_failures FROM accounts a LEFT JOIN poll_state p ON p.account_id = a.id "
            "ORDER BY a.status = 'active' DESC, a.label"
        )
        accounts = cur.fetchall()

        cur.execute(
            "SELECT al.sent_at, ac.label, al.severity, al.rule, al.acknowledged_at "
            "FROM alerts al JOIN accounts ac ON ac.id = al.account_id ORDER BY al.sent_at DESC LIMIT 50"
        )
        alerts = cur.fetchall()

        cur.execute("SELECT severity, acknowledged_at, sent_at FROM alerts WHERE sent_at >= ?", (cutoff,))
        recent = cur.fetchall()
        cur.execute(
            "SELECT count(*) c FROM alerts WHERE severity='CRITICAL' AND acknowledged_at IS NULL"
        )
        unacked_crit = cur.fetchone()["c"]

    total_accounts = len(accounts)
    active = [a for a in accounts if a["status"] == "active"]
    active_accounts = len(active)
    disabled_accounts = total_accounts - active_accounts
    healthy = sum(1 for a in active if a["last_success_at"] and (a["consecutive_failures"] or 0) == 0)
    health_pct = round(100 * healthy / active_accounts) if active_accounts else 100

    crit_24h = sum(1 for r in recent if r["severity"] == "CRITICAL")
    high_24h = sum(1 for r in recent if r["severity"] == "HIGH")
    medlow_24h = sum(1 for r in recent if r["severity"] in ("MEDIUM", "LOW"))
    alerts_24h = len(recent)

    account_rows = "".join(
        f"<tr><td class='lbl'>{html.escape(r['label'])}</td>"
        f"<td class='mono'>{r['topology']}</td>"
        f"<td>{_status_pill(r['status'])}</td>"
        f"<td class='mono'>{(r['last_success_at'] or '—')[:19].replace('T', ' ')}</td>"
        f"<td>{_fail_cell(r['consecutive_failures'] or 0)}</td>"
        f"<td>{_fail_cell(r['consecutive_auth_failures'] or 0)}</td></tr>"
        for r in accounts
    ) or "<tr><td colspan=6 class='empty'>No accounts onboarded yet</td></tr>"

    alert_rows = "".join(
        f"<tr><td class='mono'>{r['sent_at'][:19].replace('T', ' ')}</td>"
        f"<td class='lbl'>{html.escape(r['label'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'>{html.escape(r['rule'])}</td>"
        f"<td class='{'ack-yes' if r['acknowledged_at'] else 'ack-no'}'>{'✓' if r['acknowledged_at'] else '—'}</td></tr>"
        for r in alerts
    ) or "<tr><td colspan=5 class='empty'>No alerts — all clear</td></tr>"

    return _PAGE.format(
        now=now.strftime("%H:%M:%S UTC"),
        total_accounts=total_accounts,
        active_accounts=active_accounts,
        disabled_accounts=disabled_accounts,
        healthy=healthy,
        health_pct=health_pct,
        unacked_crit=unacked_crit,
        crit_cls="crit" if unacked_crit else "ok",
        crit_24h=crit_24h,
        alerts_24h=alerts_24h,
        high_24h=high_24h,
        medlow_24h=medlow_24h,
        accounts=account_rows,
        alerts=alert_rows,
    )


def serve_dashboard(db: Database, port: int) -> ThreadingHTTPServer:
    """Internal ops page - bind behind the VPN/security group, it has no
    auth of its own by design (role-based dashboard access is a Phase 3+
    item; until then network-level access control is the boundary)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Serve the styled metrics view on the dashboard's own port too,
            # so one SSH tunnel (8390) reaches both pages. Port 9109 stays
            # up separately for Prometheus scrapers, which need the raw
            # exposition format, not this HTML.
            if self.path.rstrip("/") == "/metrics":
                from sentinel.metrics import render_metrics_html

                body = render_metrics_html(dash_port=port).encode()
            elif self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            else:
                body = _render(db).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep stdout JSON-clean
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    return server
