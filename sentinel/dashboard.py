from __future__ import annotations

import html
import logging
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sentinel.crypto import CredentialCipher
from sentinel.db import Database

logger = logging.getLogger("sentinel.dashboard")

# Plain CSS constant (single braces): pages are assembled by concatenation, so
# the stylesheet never passes through str.format / f-strings and needs no
# brace-escaping.
_STYLE = """
  :root {
    --bg:#0b0813; --panel:#140f20; --panel-2:#191229; --line:#2b2140; --line-soft:#211935;
    --txt:#ece8f4; --muted:#9a8fb5; --faint:#6b5f8a; --accent:#8b5cf6; --gold:#f0b429;
    --ok:#34d399; --crit:#ff5d6c; --high:#ffa63d; --med:#ffd23d; --low:#8a7fa5;
  }
  * { box-sizing: border-box; }
  body {
    margin:0;
    background:
      radial-gradient(1200px 600px at 85% -10%, rgba(139,92,246,.13), transparent 60%),
      radial-gradient(900px 500px at 0% 0%, rgba(240,180,41,.06), transparent 55%),
      var(--bg);
    color:var(--txt); font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    -webkit-font-smoothing:antialiased; letter-spacing:.1px; min-height:100vh;
  }
  .wrap { max-width:1120px; margin:0 auto; padding:40px 32px 64px; }
  header { display:flex; align-items:center; justify-content:space-between; margin-bottom:34px; }
  .brand { display:flex; align-items:center; gap:14px; }
  .mark { width:40px; height:40px; border-radius:12px; background:linear-gradient(145deg,#1c1230,#0d0918);
    border:1px solid rgba(139,92,246,.45); display:grid; place-items:center;
    box-shadow:0 6px 22px rgba(139,92,246,.30), inset 0 1px 0 rgba(255,255,255,.08); }
  .mark svg { width:24px; height:24px; }
  .brand h1 { font-size:19px; font-weight:650; margin:0; letter-spacing:.2px; }
  .brand h1 .minas { color:var(--gold); font-weight:780; letter-spacing:3px; }
  .brand .sub { font-size:12px; color:var(--faint); margin-top:2px; }
  .nav { display:flex; align-items:center; gap:10px; }
  .live { display:inline-flex; align-items:center; gap:9px; font-size:12.5px; color:var(--muted);
    background:var(--panel); border:1px solid var(--line); padding:8px 14px; border-radius:999px; text-decoration:none; }
  a.live:hover { border-color:var(--accent); color:var(--txt); }
  a.live.primary { color:#0b0813; background:linear-gradient(145deg,var(--gold),#e0a020); border-color:transparent; font-weight:650; }
  a.live.primary:hover { filter:brightness(1.08); color:#0b0813; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--ok); box-shadow:0 0 0 0 rgba(52,211,153,.6); animation:pulse 2.2s infinite; }
  @keyframes pulse { 0% { box-shadow:0 0 0 0 rgba(52,211,153,.55); } 70% { box-shadow:0 0 0 8px rgba(52,211,153,0); } 100% { box-shadow:0 0 0 0 rgba(52,211,153,0); } }
  .kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:30px; }
  .kpi { background:linear-gradient(180deg,var(--panel-2),var(--panel)); border:1px solid var(--line); border-radius:16px; padding:18px 20px; position:relative; overflow:hidden; }
  .kpi::after { content:""; position:absolute; inset:0 0 auto 0; height:2px; background:linear-gradient(90deg,transparent,rgba(139,92,246,.6),rgba(240,180,41,.5),transparent); opacity:.6; }
  .kpi .label { font-size:11.5px; text-transform:uppercase; letter-spacing:.8px; color:var(--faint); }
  .kpi .val { font-size:32px; font-weight:680; margin-top:8px; font-variant-numeric:tabular-nums; line-height:1; }
  .kpi .val.crit { color:var(--crit); } .kpi .val.ok { color:var(--ok); }
  .kpi .foot { font-size:12px; color:var(--muted); margin-top:8px; }
  section { margin-top:26px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:16px; overflow:hidden; }
  .card-head { display:flex; align-items:baseline; justify-content:space-between; padding:16px 22px; border-bottom:1px solid var(--line-soft); }
  .card-head h2 { font-size:14px; font-weight:620; margin:0; letter-spacing:.3px; }
  .card-head .count { font-size:12px; color:var(--faint); font-variant-numeric:tabular-nums; }
  table { width:100%; border-collapse:collapse; }
  thead th { font-size:11px; text-transform:uppercase; letter-spacing:.7px; color:var(--faint); text-align:left; padding:12px 22px; font-weight:600; }
  tbody td { padding:14px 22px; font-size:13.5px; border-top:1px solid var(--line-soft); }
  tbody tr:hover { background:rgba(139,92,246,.05); }
  .mono { font-family:"SF Mono","JetBrains Mono",ui-monospace,Menlo,monospace; font-size:12.5px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .rule { font-family:"SF Mono",ui-monospace,Menlo,monospace; font-size:12.5px; }
  .lbl { font-weight:550; }
  .pill { display:inline-flex; align-items:center; gap:6px; font-size:11.5px; font-weight:600; padding:4px 11px; border-radius:999px; letter-spacing:.3px; }
  .pill::before { content:""; width:6px; height:6px; border-radius:50%; background:currentColor; }
  .s-active { color:var(--ok); background:rgba(52,211,153,.12); }
  .s-disabled { color:var(--faint); background:rgba(91,104,131,.15); }
  .s-tamper { color:var(--crit); background:rgba(255,93,108,.12); }
  .sev-CRITICAL { color:var(--crit); background:rgba(255,93,108,.13); }
  .sev-HIGH { color:var(--high); background:rgba(255,166,61,.13); }
  .sev-MEDIUM { color:var(--med); background:rgba(255,210,61,.13); }
  .sev-LOW { color:var(--low); background:rgba(124,138,165,.15); }
  .ack-yes { color:var(--ok); } .ack-no { color:var(--faint); }
  .fail { color:var(--high); font-weight:600; } .zero { color:var(--faint); }
  .empty { padding:34px; text-align:center; color:var(--faint); font-size:13px; }
  footer { margin-top:28px; text-align:center; color:var(--faint); font-size:11.5px; }
  a.acctlink { color:var(--txt); text-decoration:none; font-weight:550; }
  a.acctlink:hover { color:var(--gold); }
  /* --- Account detail --- */
  .cols { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .dl { display:grid; grid-template-columns:150px 1fr; gap:11px 16px; padding:20px 22px; font-size:13.5px; }
  .dl dt { color:var(--faint); font-size:12px; }
  .dl dd { margin:0; color:var(--txt); word-break:break-word; }
  .dl dd.dim { color:var(--faint); }
  .checks { padding:6px 22px 18px; }
  .check { display:flex; align-items:center; gap:11px; padding:11px 0; border-top:1px solid var(--line-soft); font-size:13.5px; }
  .check:first-child { border-top:none; }
  .check .ic { width:19px; height:19px; border-radius:50%; display:grid; place-items:center; font-size:11px; flex:none; font-weight:700; }
  .check .ic.ok { background:rgba(52,211,153,.15); color:var(--ok); }
  .check .ic.warn { background:rgba(255,166,61,.15); color:var(--high); }
  .check .ic.na { background:rgba(139,92,246,.12); color:var(--accent); }
  .check .txt { flex:1; } .check .meta { color:var(--faint); font-size:12px; text-align:right; }
  .det { display:flex; align-items:center; gap:12px; padding:9px 22px; border-top:1px solid var(--line-soft); font-size:13px; }
  .det .desc { color:var(--muted); }
  @media (max-width:720px) { .cols { grid-template-columns:1fr; } }
  /* --- Add-customer form --- */
  form.addf { padding:22px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px 20px; }
  .fld { display:flex; flex-direction:column; gap:6px; }
  .fld.full { grid-column:1 / -1; }
  .fld label { font-size:11.5px; text-transform:uppercase; letter-spacing:.6px; color:var(--faint); }
  .fld label .req { color:var(--gold); }
  .fld input, .fld select { background:#0d0a16; border:1px solid var(--line); border-radius:10px; color:var(--txt);
    padding:10px 12px; font-size:14px; font-family:inherit; }
  .fld input:focus, .fld select:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px rgba(139,92,246,.18); }
  .fld .hint { font-size:11px; color:var(--faint); }
  .grouphdr { grid-column:1 / -1; font-size:11px; text-transform:uppercase; letter-spacing:.7px; color:var(--accent);
    margin-top:8px; padding-top:14px; border-top:1px solid var(--line-soft); }
  .actions { display:flex; align-items:center; gap:14px; margin-top:24px; }
  button.submit { background:linear-gradient(145deg,var(--gold),#e0a020); color:#0b0813; border:none; border-radius:10px;
    padding:12px 22px; font-size:14px; font-weight:680; cursor:pointer; letter-spacing:.2px; }
  button.submit:hover { filter:brightness(1.08); }
  .banner { padding:14px 18px; border-radius:12px; margin-bottom:20px; font-size:13.5px; }
  .banner.err { background:rgba(255,93,108,.12); border:1px solid rgba(255,93,108,.4); color:#ffb3bb; }
  .banner.ok { background:rgba(52,211,153,.12); border:1px solid rgba(52,211,153,.4); color:#9df0cf; }
  .banner code { font-family:"SF Mono",ui-monospace,Menlo,monospace; }
  @media (max-width:720px) { .kpis { grid-template-columns:repeat(2,1fr); } .wrap { padding:24px 16px; } .grid2 { grid-template-columns:1fr; } }
"""

_MARK_SVG = (
    '<div class="mark"><svg viewBox="0 0 24 24" fill="none"><defs><linearGradient id="inf" x1="3" y1="20" '
    'x2="21" y2="4" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#f0b429"/><stop offset=".48" '
    'stop-color="#f0b429"/><stop offset=".58" stop-color="#8b5cf6"/><stop offset="1" stop-color="#6d28d9"/>'
    '</linearGradient></defs><path fill="url(#inf)" d="M18.6 6.62c-1.44 0-2.8.56-3.77 1.53L12 10.66 10.48 12h.01'
    'L7.8 14.39c-.64.64-1.49.99-2.4.99-1.87 0-3.39-1.51-3.39-3.38S3.53 8.62 5.4 8.62c.91 0 1.76.35 2.44 1.03l1.13 1 '
    '1.51-1.34L9.22 8.2C8.2 7.18 6.84 6.62 5.4 6.62 2.42 6.62 0 9.04 0 12s2.42 5.38 5.4 5.38c1.44 0 2.8-.56 3.77-1.53'
    'l2.83-2.5.01.01L13.52 12h-.01l2.69-2.39c.64-.64 1.49-.99 2.4-.99 1.87 0 3.39 1.51 3.39 3.38s-1.52 3.38-3.39 3.38'
    'c-.9 0-1.76-.35-2.44-1.03l-1.14-1.01-1.51 1.34 1.27 1.12c1.02 1.01 2.37 1.57 3.82 1.57 2.98 0 5.4-2.41 5.4-5.38'
    's-2.42-5.38-5.4-5.38z"/></svg></div>'
)


# Timestamps are stored and rendered in UTC (the forensic source of truth);
# this converts them to the viewer's own timezone in the browser, so Erwin
# sees Toronto time and a VA sees theirs. Elements opt in via
# <span class="ts" data-utc="<iso>">. The header clock ticks in local time.
_LOCAL_TIME_SCRIPT = """<script>
(function () {
  var fmt = new Intl.DateTimeFormat('sv-SE', {year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',second:'2-digit'});
  document.querySelectorAll('.ts[data-utc]').forEach(function (el) {
    var iso = el.dataset.utc;
    if (!/[+Zz]/.test(iso.slice(10))) iso += 'Z';
    var d = new Date(iso);
    if (!isNaN(d)) el.textContent = fmt.format(d);
  });
  function tzShort(now) {
    var p = new Intl.DateTimeFormat(undefined, {timeZoneName:'short'}).formatToParts(now);
    for (var i = 0; i < p.length; i++) if (p[i].type === 'timeZoneName') return p[i].value;
    return '';
  }
  var clock = document.getElementById('liveclock');
  if (clock) {
    var tick = function () {
      var now = new Date();
      clock.textContent = fmt.format(now).slice(11) + ' ' + tzShort(now);
    };
    tick();
    setInterval(tick, 1000);
  }
})();
</script>"""


def _ts(iso: str | None, fallback: str = "—") -> str:
    """A timestamp cell: UTC as server-rendered fallback, upgraded to the
    viewer's local timezone by _LOCAL_TIME_SCRIPT."""
    if not iso:
        return fallback
    return f'<span class="ts" data-utc="{html.escape(iso)}">{html.escape(iso[:19].replace("T", " "))}</span>'


def _page(nav_html: str, body_html: str, refresh: bool = True) -> str:
    refresh_meta = '<meta http-equiv="refresh" content="30">' if refresh else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Minas AI Guardian</title>" + refresh_meta + "<style>" + _STYLE + "</style></head><body>"
        '<div class="wrap"><header><div class="brand">' + _MARK_SVG
        + '<div><h1><span class="minas">MINAS</span> AI Guardian</h1>'
        '<div class="sub">Multi-exchange account-compromise monitoring</div></div></div>'
        '<div class="nav">' + nav_html + "</div></header>"
        + body_html
        + "<footer>Minas AI Guardian · internal ops view · network-gated (Tailscale)</footer></div>"
        + _LOCAL_TIME_SCRIPT + "</body></html>"
    )


# The protective detections that watch every account (the account's "safety
# net"). Shown on the account detail page. Kept in sync with sentinel/detectors.
_DETECTION_CATALOG = [
    ("CRITICAL", "withdrawal_initiated", "Any withdrawal — including in-flight/pending"),
    ("CRITICAL", "withdrawal_to_unseen_address", "Withdrawal to a never-before-seen address"),
    ("CRITICAL", "unexplained_quantity_decrease", "Asset drop not explained by a trade or withdrawal"),
    ("HIGH", "withdrawal_burst", "Several withdrawals in a short window (structuring)"),
    ("HIGH", "transfer_route_never_seen", "Transfer along a route this account never used"),
    ("HIGH", "never_touched_pair_activity", "Trading a pair this account never traded"),
    ("HIGH", "off_market_execution", "Fill far from market price (counterparty drain)"),
    ("HIGH", "monitoring_key_tamper_suspected", "Our own read-only key failing auth (tamper)"),
    ("HIGH", "account_permission_state_changed", "Permission / IP-allowlist drift (proxy)"),
    ("MEDIUM", "abnormal_fee_spend", "Fee spend spikes vs the account's baseline"),
    ("MEDIUM", "account_unreachable", "Account unreachable for several polls"),
    ("LOW", "baseline_drift_new_deposit_source", "A new deposit-source address appears"),
]


def _account_type_label(topology: str | None) -> str:
    """Customer-facing name for the stored topology value."""
    return {"independent": "Primary", "sub_account": "Sub-account"}.get(topology or "", topology or "—")


def _status_pill(status: str) -> str:
    cls = {"active": "s-active", "disabled": "s-disabled", "tamper_suspected": "s-tamper"}.get(status, "s-disabled")
    return f'<span class="pill {cls}">{html.escape(status.replace("_", " "))}</span>'


def _fail_cell(n: int) -> str:
    return f'<span class="fail">{n}</span>' if n else '<span class="zero">0</span>'


def _render(db: Database) -> str:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()

    with db.cursor() as cur:
        cur.execute(
            "SELECT a.id, a.label, a.topology, a.status, p.last_success_at, p.consecutive_failures, "
            "p.consecutive_auth_failures, a.exchange FROM accounts a LEFT JOIN poll_state p ON p.account_id = a.id "
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
        cur.execute("SELECT count(*) c FROM alerts WHERE severity='CRITICAL' AND acknowledged_at IS NULL")
        unacked_crit = cur.fetchone()["c"]

    total = len(accounts)
    active = [a for a in accounts if a["status"] == "active"]
    n_active = len(active)
    healthy = sum(1 for a in active if a["last_success_at"] and (a["consecutive_failures"] or 0) == 0)
    health_pct = round(100 * healthy / n_active) if n_active else 100
    crit_24h = sum(1 for r in recent if r["severity"] == "CRITICAL")
    high_24h = sum(1 for r in recent if r["severity"] == "HIGH")
    medlow_24h = sum(1 for r in recent if r["severity"] in ("MEDIUM", "LOW"))

    account_rows = "".join(
        f"<tr><td><a class='acctlink' href='/account?id={r['id']}'>{html.escape(r['label'])} ›</a></td>"
        f"<td class='mono'>{html.escape((r['exchange'] if 'exchange' in r.keys() else 'bitget') or 'bitget')}</td>"
        f"<td class='mono'>{_account_type_label(r['topology'])}</td><td>{_status_pill(r['status'])}</td>"
        f"<td class='mono'>{_ts(r['last_success_at'])}</td>"
        f"<td>{_fail_cell(r['consecutive_failures'] or 0)}</td>"
        f"<td>{_fail_cell(r['consecutive_auth_failures'] or 0)}</td></tr>"
        for r in accounts
    ) or "<tr><td colspan=7 class='empty'>No accounts onboarded yet</td></tr>"

    alert_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['sent_at'])}</td>"
        f"<td class='lbl'>{html.escape(r['label'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'>{html.escape(r['rule'])}</td>"
        f"<td class='{'ack-yes' if r['acknowledged_at'] else 'ack-no'}'>{'✓' if r['acknowledged_at'] else '—'}</td></tr>"
        for r in alerts
    ) or "<tr><td colspan=5 class='empty'>No alerts — all clear</td></tr>"

    nav = (
        '<a class="live primary" href="/add">+ Add customer</a>'
        '<a class="live" href="/metrics">Metrics</a>'
        f'<span class="live"><span class="dot"></span> Live · <span id="liveclock">{now.strftime("%H:%M:%S UTC")}</span></span>'
    )
    body = (
        '<div class="kpis">'
        f'<div class="kpi"><div class="label">Accounts monitored</div><div class="val">{total}</div>'
        f'<div class="foot">{n_active} active · {total - n_active} disabled</div></div>'
        f'<div class="kpi"><div class="label">Fleet health</div><div class="val ok">{health_pct}%</div>'
        f'<div class="foot">{healthy} of {n_active} reporting</div></div>'
        f'<div class="kpi"><div class="label">Unacknowledged critical</div>'
        f'<div class="val {"crit" if unacked_crit else "ok"}">{unacked_crit}</div>'
        f'<div class="foot">{crit_24h} critical in last 24h</div></div>'
        f'<div class="kpi"><div class="label">Alerts · 24h</div><div class="val">{len(recent)}</div>'
        f'<div class="foot">{high_24h} high · {medlow_24h} med/low</div></div></div>'
        '<section><div class="card"><div class="card-head"><h2>Accounts</h2>'
        f'<span class="count">{total} total</span></div><table><thead><tr><th>Account</th><th>Exchange</th>'
        "<th>Account type</th><th>Status</th><th>Last success</th><th>Failures</th><th>Auth</th></tr></thead>"
        f"<tbody>{account_rows}</tbody></table></div></section>"
        '<section><div class="card"><div class="card-head"><h2>Recent alerts</h2>'
        '<span class="count">latest 50</span></div><table><thead><tr><th>Time</th><th>Account</th>'
        f"<th>Severity</th><th>Rule</th><th>Ack</th></tr></thead><tbody>{alert_rows}</tbody></table></div></section>"
    )
    return _page(nav, body)


def _dd(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else '<span class="dim">—</span>'


def _check(ok: str, text: str, meta: str = "", meta_is_html: bool = False) -> str:
    icon = {"ok": '<span class="ic ok">✓</span>', "warn": '<span class="ic warn">!</span>',
            "na": '<span class="ic na">•</span>'}[ok]
    meta_html = f'<span class="meta">{meta if meta_is_html else html.escape(meta)}</span>' if meta else ""
    return f'<div class="check">{icon}<span class="txt">{text}</span>{meta_html}</div>'


def _render_account(db: Database, account_id: int) -> str | None:
    now = datetime.now(timezone.utc)
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        a = cur.fetchone()
        if a is None:
            return None
        keys = a.keys()

        def f(k):
            return a[k] if k in keys else None

        cur.execute("SELECT * FROM poll_state WHERE account_id = ?", (account_id,))
        ps = cur.fetchone()
        cur.execute("SELECT count(*) c FROM known_addresses WHERE account_id = ?", (account_id,))
        n_addr = cur.fetchone()["c"]
        cur.execute("SELECT count(*) c FROM traded_pairs WHERE account_id = ?", (account_id,))
        n_pairs = cur.fetchone()["c"]
        cur.execute("SELECT count(*) c FROM seen_withdrawals WHERE account_id = ?", (account_id,))
        n_wd = cur.fetchone()["c"]
        cur.execute("SELECT count(*) c FROM permission_snapshots WHERE account_id = ?", (account_id,))
        has_perm = cur.fetchone()["c"] > 0
        cur.execute(
            "SELECT count(DISTINCT coin) c FROM balance_snapshots WHERE account_id = ? "
            "AND snapshot_at = (SELECT MAX(snapshot_at) FROM balance_snapshots WHERE account_id = ?)",
            (account_id, account_id),
        )
        n_assets = cur.fetchone()["c"]
        cur.execute(
            "SELECT event_type, severity, created_at FROM events WHERE account_id = ? ORDER BY created_at DESC LIMIT 20",
            (account_id,),
        )
        events = cur.fetchall()
        cur.execute(
            "SELECT sent_at, severity, rule, acknowledged_at FROM alerts WHERE account_id = ? ORDER BY sent_at DESC LIMIT 20",
            (account_id,),
        )
        alerts = cur.fetchall()

    label = f("label")
    status = f("status")
    last_ok = ps["last_success_at"] if ps else None
    cons_fail = (ps["consecutive_failures"] if ps else 0) or 0
    cons_auth = (ps["consecutive_auth_failures"] if ps else 0) or 0

    # Freshness of monitoring
    live = "warn"
    live_meta = "no successful poll yet"
    if last_ok:
        try:
            age = (now - datetime.fromisoformat(last_ok)).total_seconds()
        except ValueError:
            age = 9999
        live = "ok" if (status == "active" and age < 300) else "warn"
        live_meta = f"last poll {_ts(last_ok)}"

    seeded = n_addr + n_pairs + n_wd > 0 or has_perm
    dests = []
    if f("alert_email"):
        dests.append(f("alert_email"))
    if f("alert_email_secondary"):
        dests.append(f("alert_email_secondary"))
    if f("alert_phone"):
        dests.append(f("alert_phone"))
    if f("alert_phone_secondary"):
        dests.append(f("alert_phone_secondary"))
    dest_meta = ", ".join(dests) if dests else "Telegram only"

    checks = (
        _check("ok", "<b>Read-only key</b> — verified at onboarding; no trade or withdraw scope",
               f("exchange") or "bitget")
        + _check("ok", "<b>Credentials encrypted at rest</b> — decrypted only in worker memory per call")
        + _check(live, "<b>Monitoring live</b> — polled continuously for fund movement & config drift", live_meta,
                 meta_is_html=last_ok is not None)
        + _check("ok" if cons_auth == 0 else "warn",
                 "<b>Tamper detection armed</b> — repeated auth failures on our key raise a HIGH alert",
                 f"{cons_auth} consecutive auth failures")
        + _check("ok" if seeded else "warn", "<b>Baseline established</b> — known addresses, traded pairs & permissions snapshot",
                 f"{n_addr} addrs · {n_pairs} pairs · {n_assets} assets · perms {'✓' if has_perm else '—'}")
        + _check("ok" if cons_fail == 0 else "warn", "<b>Reachable</b> — account responding to polls",
                 f"{cons_fail} consecutive failures")
        + _check("na", "<b>Alert routing</b> — CRITICAL/HIGH go to Telegram + team group" +
                 (" + per-account contacts" if dests else ""), dest_meta)
    )

    detections = "".join(
        f'<div class="det"><span class="pill sev-{sev}">{sev}</span>'
        f'<span class="rule">{html.escape(rule)}</span><span class="desc">{html.escape(desc)}</span></div>'
        for sev, rule, desc in _DETECTION_CATALOG
    )

    event_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['created_at'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'>{html.escape(r['event_type'])}</td></tr>"
        for r in events
    ) or "<tr><td colspan=3 class='empty'>No events logged yet</td></tr>"

    alert_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['sent_at'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'>{html.escape(r['rule'])}</td>"
        f"<td class='{'ack-yes' if r['acknowledged_at'] else 'ack-no'}'>{'✓' if r['acknowledged_at'] else '—'}</td></tr>"
        for r in alerts
    ) or "<tr><td colspan=4 class='empty'>No alerts — all clear</td></tr>"

    nav = '<a class="live" href="/">← Dashboard</a>'
    body = (
        '<section><div class="card"><div class="card-head">'
        f'<h2>{html.escape(f("customer_name") or label)}</h2>'
        f'<span class="count">{html.escape(label)} · {_status_pill(status)}</span></div></div></section>'
        '<section class="cols">'
        '<div class="card"><div class="card-head"><h2>Customer</h2></div><dl class="dl">'
        f"<dt>Name</dt><dd>{_dd(f('customer_name'))}</dd>"
        f"<dt>License</dt><dd>{_dd(f('license'))}</dd>"
        f"<dt>Email</dt><dd>{_dd(f('alert_email'))}</dd>"
        f"<dt>Phone / text</dt><dd>{_dd(f('alert_phone'))}</dd>"
        f"<dt>Secondary email</dt><dd>{_dd(f('alert_email_secondary'))}</dd>"
        f"<dt>Secondary phone</dt><dd>{_dd(f('alert_phone_secondary'))}</dd>"
        f"<dt>Telegram</dt><dd>{_dd(f('telegram_handle'))}</dd>"
        f"<dt>Secondary Telegram</dt><dd>{_dd(f('telegram_handle_secondary'))}</dd>"
        "</dl></div>"
        '<div class="card"><div class="card-head"><h2>Account</h2></div><dl class="dl">'
        f"<dt>Label</dt><dd>{_dd(label)}</dd>"
        f"<dt>Exchange</dt><dd>{_dd(f('exchange'))}</dd>"
        f"<dt>Account type</dt><dd>{_dd(_account_type_label(f('topology')))}</dd>"
        f"<dt>Status</dt><dd>{_status_pill(status)}</dd>"
        f"<dt>Onboarded</dt><dd>{_ts(f('onboarded_at'))}</dd>"
        f"<dt>Last poll</dt><dd>{_dd((last_ok or '')[:19].replace('T', ' '))}</dd>"
        "</dl></div></section>"
        '<section><div class="card"><div class="card-head"><h2>Security posture</h2>'
        '<span class="count">what keeps this account safe</span></div>'
        f'<div class="checks">{checks}</div></div></section>'
        '<section><div class="card"><div class="card-head"><h2>Active detections</h2>'
        f'<span class="count">{len(_DETECTION_CATALOG)} rules watching this account</span></div>{detections}</div></section>'
        '<section class="cols">'
        '<div class="card"><div class="card-head"><h2>Recent events</h2><span class="count">latest 20</span></div>'
        f'<table><thead><tr><th>Time</th><th>Sev</th><th>Event</th></tr></thead><tbody>{event_rows}</tbody></table></div>'
        '<div class="card"><div class="card-head"><h2>Alerts</h2><span class="count">latest 20</span></div>'
        f'<table><thead><tr><th>Time</th><th>Sev</th><th>Rule</th><th>Ack</th></tr></thead><tbody>{alert_rows}</tbody></table></div>'
        "</section>"
    )
    return _page(nav, body, refresh=False)


def _input(name: str, label: str, values: dict, kind: str = "text", required: bool = False, hint: str = "") -> str:
    val = html.escape(values.get(name, "") or "")
    star = ' <span class="req">*</span>' if required else ""
    hint_html = f'<span class="hint">{html.escape(hint)}</span>' if hint else ""
    req = " required" if required else ""
    return (
        f'<div class="fld"><label>{html.escape(label)}{star}</label>'
        f'<input type="{kind}" name="{name}" value="{val}"{req} autocomplete="off">{hint_html}</div>'
    )


def _render_add_form(values: dict | None = None, error: str | None = None, ok: str | None = None) -> str:
    values = values or {}
    ex = values.get("exchange", "bitget")
    topo = values.get("topology", "independent")
    ex_opts = "".join(
        f'<option value="{e}"{" selected" if ex == e else ""}>{e}</option>'
        for e in ("bitget", "bybit", "okx", "binance")
    )
    topo_opts = (
        f'<option value="independent"{" selected" if topo == "independent" else ""}>Primary</option>'
        f'<option value="sub_account"{" selected" if topo == "sub_account" else ""}>Sub-account</option>'
    )
    banner = ""
    if error:
        banner = f'<div class="banner err">✕ {html.escape(error)}</div>'
    elif ok:
        banner = f'<div class="banner ok">{ok}</div>'  # ok is pre-escaped/safe HTML

    body = (
        banner + '<div class="card"><div class="card-head"><h2>Add customer</h2>'
        '<span class="count">read-only key is verified before storing</span></div>'
        '<form class="addf" method="post" action="/add"><div class="grid2">'
        + _input("name", "Name", values, required=True)
        + f'<div class="fld"><label>License</label><input type="text" name="license" '
        f'value="{html.escape(values.get("license", "") or "")}" autocomplete="off"></div>'
        '<div class="grouphdr">Contacts</div>'
        + _input("email", "Email", values, "email")
        + _input("phone", "Phone / text", values, "tel", hint="E.164, e.g. +16477216549")
        + _input("email_secondary", "Secondary email", values, "email")
        + _input("phone_secondary", "Secondary phone / text", values, "tel")
        + _input("telegram", "Telegram handle", values)
        + _input("telegram_secondary", "Secondary Telegram handle", values)
        + '<div class="grouphdr">Exchange &amp; API key (read-only)</div>'
        f'<div class="fld"><label>Exchange <span class="req">*</span></label>'
        f'<select name="exchange">{ex_opts}</select></div>'
        f'<div class="fld"><label>Account type</label><select name="topology">{topo_opts}</select></div>'
        + _input("passphrase", "API passphrase", {}, "password", hint="Bitget/OKX only — leave blank for Bybit/Binance")
        + _input("api_key", "Access API key", {}, "password", required=True)
        + _input("secret", "Secret key", {}, "password", required=True)
        + '</div><div class="actions"><button class="submit" type="submit">Verify &amp; add customer</button>'
        '<span class="hint">The key is checked for read-only scope on the exchange; a trade/withdraw key is rejected.</span>'
        "</div></form></div>"
    )
    nav = '<a class="live" href="/">← Dashboard</a>'
    # No auto-refresh on the form (don't wipe a half-filled form).
    return _page(nav, body, refresh=False)


def _render_result(label: str, exchange: str, account_id: int) -> str:
    ok = (
        f"✓ Verified read-only and added <code>{html.escape(label)}</code> "
        f"({html.escape(exchange)}) as account id {account_id}. It will begin polling within a minute."
    )
    return _render_add_form(ok=ok)


def serve_dashboard(db: Database, cipher: CredentialCipher, port: int) -> ThreadingHTTPServer:
    """Internal ops page. Network-gated (Tailscale) — it has no login of its
    own by design. The /add form runs the same fail-closed read-only gate as
    the CLI, so a trade/withdraw key can never be stored through it."""

    def _same_origin(handler) -> bool:
        origin = handler.headers.get("Origin")
        if not origin:
            return True  # non-browser or same-origin form post without Origin
        return urlparse(origin).netloc == (handler.headers.get("Host") or "")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/metrics/raw":
                from sentinel.metrics import raw_exposition

                ctype, body = raw_exposition()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/metrics":
                from sentinel.metrics import render_metrics_html

                self._send(render_metrics_html(dash_port=port).encode())
            elif path == "/add":
                self._send(_render_add_form().encode())
            elif path == "/account":
                qs = parse_qs(urlparse(self.path).query)
                try:
                    aid = int((qs.get("id", ["0"])[0]))
                except ValueError:
                    aid = 0
                page = _render_account(db, aid)
                if page is None:
                    self._send(_page('<a class="live" href="/">← Dashboard</a>',
                                     '<section><div class="card"><div class="empty">Account not found</div></div></section>',
                                     refresh=False).encode(), 404)
                else:
                    self._send(page.encode())
            elif self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self._send(_render(db).encode())

        def do_POST(self):
            if urlparse(self.path).path.rstrip("/") != "/add":
                self._send(b"not found", 404)
                return
            if not _same_origin(self):
                self._send(b"cross-origin POST rejected", 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 64 * 1024:
                self._send(_render_add_form(error="empty or oversized submission").encode(), 400)
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"))

            def g(k: str) -> str:
                return (form.get(k, [""])[0] or "").strip()

            # Preserve non-secret fields for re-render on error; never echo secrets.
            values = {k: g(k) for k in (
                "name", "license", "email", "phone", "email_secondary", "phone_secondary",
                "telegram", "telegram_secondary", "exchange", "topology",
            )}

            if not g("name") or not g("api_key") or not g("secret"):
                self._send(_render_add_form(values, error="Name, API key and secret are required.").encode(), 400)
                return

            from sentinel.onboarding import OnboardingRejected, generate_unique_label, onboard_account

            try:
                label = generate_unique_label(db, g("name"))
                result = onboard_account(
                    db, cipher, label=label, topology=g("topology") or "independent",
                    exchange=g("exchange") or "bitget", api_key=g("api_key"), secret=g("secret"),
                    passphrase=g("passphrase"), customer_name=g("name"),
                    alert_email=g("email") or None, alert_phone=g("phone") or None,
                    alert_email_secondary=g("email_secondary") or None,
                    alert_phone_secondary=g("phone_secondary") or None,
                    telegram_handle=g("telegram") or None,
                    telegram_handle_secondary=g("telegram_secondary") or None,
                    license=g("license") or None,
                )
                self._send(_render_result(label, g("exchange") or "bitget", result.account_id).encode())
            except OnboardingRejected as exc:
                self._send(_render_add_form(values, error=str(exc)).encode(), 200)
            except ValueError as exc:
                self._send(_render_add_form(values, error=str(exc)).encode(), 400)
            except Exception:  # noqa: BLE001 - never leak an exchange error verbatim (may echo creds)
                logger.exception("add-customer onboarding failed")
                self._send(_render_add_form(
                    values, error="Onboarding failed unexpectedly - check the service logs (secrets redacted)."
                ).encode(), 500)

        def log_message(self, *args):  # keep stdout JSON-clean
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    return server
