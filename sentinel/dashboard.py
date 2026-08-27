from __future__ import annotations

import html
import logging
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sentinel.buildinfo import version_line
from sentinel.crypto import CredentialCipher
from sentinel.db import Database
from sentinel.events import append_event
from sentinel.operators import (
    SESSION_COOKIE,
    check_operator_login,
    make_pending_token,
    make_session_token,
    operator_count,
    record_operator_event,
    session_operator,
    session_secret,
    verify_pending_token,
)
from sentinel.passwords import LoginThrottle
from sentinel.rules_schema import default_for

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
  /* Member acknowledgement button (G-22). Deliberately quiet: it sits in every
     unacknowledged row, and a row of loud buttons reads as the page asking to
     be clicked rather than as a list of things that happened. */
  .ackbtn { font:inherit; font-size:12px; cursor:pointer; padding:3px 10px;
            border:1px solid var(--line); border-radius:6px;
            background:transparent; color:var(--txt); }
  .ackbtn:hover { border-color:var(--ok); color:var(--ok); }
  .snoozesel { font:inherit; font-size:12px; margin-right:6px; padding:3px 6px;
               border:1px solid var(--line); border-radius:6px;
               background:var(--panel-2); color:var(--txt); }
  .snoozed { font-size:12px; color:var(--muted); }
  .fail { color:var(--high); font-weight:600; } .zero { color:var(--faint); }
  /* An incident page is handed to an exchange, an insurer or the police, so it
     has to survive being printed. Export is the browser's own print-to-PDF
     rather than a generated file: a PDF library is a new dependency in a
     security product, server-side rendering drifts from what the customer
     actually saw, and the browser's footer carries the URL and a timestamp -
     better provenance for evidence than a document we composed ourselves. */
  @media print {
    :root { --bg:#fff; --panel:#fff; --panel-2:#fff; --txt:#111; --muted:#444;
            --faint:#666; --line:#bbb; --line-soft:#ddd; }
    body { background:#fff !important; color:#111; }
    .wrap { max-width:none; padding:0; }
    .noprint, .nav form, .ackbtn, .snoozesel { display:none !important; }
    .card { border:1px solid #bbb; break-inside:avoid; }
    .kpi::after, .dot { display:none; }
    a { color:#111; text-decoration:none; }
  }
  .empty { padding:34px; text-align:center; color:var(--faint); font-size:13px; }
  footer { margin-top:28px; text-align:center; color:var(--faint); font-size:11.5px; }
  footer .build { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px; opacity:.85; }
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
        # The build line is here rather than tucked in a status page because
        # "what is deployed" is a question asked while looking at something
        # else - an alert that should have fired, a fix that should have
        # landed. Showing it on every page means the answer is never a guess
        # about whether the last deploy took.
        + '<footer>Minas AI Guardian · internal ops view · network-gated (Tailscale)'
        + f'<br><span class="build">{html.escape(version_line())}</span></footer></div>'
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


def _watch_cell(row, threshold: int) -> str:
    """Whether this account is actually being watched right now (G-6).

    "The account shows as 'not being watched' everywhere it appears" - and the
    dashboard is one of those places. Derived from the same counters the alert
    fires on, so the page and the alert cannot disagree."""
    failures = (row["consecutive_failures"] or 0) + (row["consecutive_auth_failures"] or 0)
    if failures >= threshold:
        return '<span class="pill s-tamper">NOT BEING WATCHED</span>'
    return '<span class="pill s-active">watched</span>'


def _fail_cell(n: int) -> str:
    return f'<span class="fail">{n}</span>' if n else '<span class="zero">0</span>'


def _problem_state(row, threshold: int) -> str:
    """One of trouble / watching / paused, for #39's sort-and-filter.

    Derived from exactly the judgements this page already renders - the
    status pill and `_watch_cell`'s counter threshold - never a third
    opinion, so the filter can only group what the row already says.
    `paused` is deliberate stillness (disabled); everything else that is
    not being watched is trouble, tamper_suspected included."""
    if row["status"] == "disabled":
        return "paused"
    if row["status"] != "active":
        return "trouble"
    failures = (row["consecutive_failures"] or 0) + (row["consecutive_auth_failures"] or 0)
    if failures >= threshold:
        return "trouble"
    return "watching"


def _render_operator_login(error: str | None = None) -> str:
    """The ops sign-in. Deliberately says nothing about which usernames exist -
    one message for a bad username and a bad password alike."""
    banner = f'<div class="banner err">✕ {html.escape(error)}</div>' if error else ""
    body = (
        banner
        + '<section><div class="card"><div class="card-head"><h2>Operator sign-in</h2>'
        '<span class="count">Minas staff only</span></div>'
        '<form class="addf" method="post" action="/login">'
        '<div class="grid2">'
        '<div class="fld"><label>Username</label>'
        '<input type="text" name="username" autocomplete="username" required></div>'
        '<div class="fld"><label>Password</label>'
        '<input type="password" name="password" autocomplete="current-password" required></div>'
        '</div><div style="margin-top:18px">'
        '<button class="submit" type="submit">Sign in</button></div>'
        "</form></div></section>"
    )
    return _page('<span class="live"><span class="dot"></span> Ops</span>', body, refresh=False)


def _render_operator_code(pending: str, error: str | None = None) -> str:
    """Step two of the sign-in: the code from the operator's app, or one of
    their recovery codes. The pending token proves the password step happened
    moments ago; nothing here says whether the username exists."""
    banner = f'<div class="banner err">✕ {html.escape(error)}</div>' if error else ""
    body = (
        banner
        + '<section><div class="card"><div class="card-head"><h2>Second factor</h2>'
        '<span class="count">Minas staff only</span></div>'
        '<form class="addf" method="post" action="/login/code">'
        f'<input type="hidden" name="pending" value="{html.escape(pending)}">'
        '<div class="fld"><label>Code from your authenticator app, or a recovery code</label>'
        '<input type="text" name="code" inputmode="numeric" autocomplete="one-time-code" required autofocus></div>'
        '<div style="margin-top:18px"><button class="submit" type="submit">Sign in</button></div>'
        "</form></div></section>"
    )
    return _page('<span class="live"><span class="dot"></span> Ops</span>', body, refresh=False)


def _render_operator_enrol(pending: str, secret: str, uri: str, error: str | None = None) -> str:
    """The first-login step (decision 19): two-factor is mandatory, so an
    operator without a confirmed enrolment cannot get past here. It is not
    in force until a code verifies - a mis-scanned secret leaves them able to
    try again, not locked out."""
    banner = f'<div class="banner err">✕ {html.escape(error)}</div>' if error else ""
    body = (
        banner
        + '<section><div class="card"><div class="card-head"><h2>Set up two-factor</h2>'
        '<span class="count">Required for every operator</span></div>'
        "<p>Add this secret to your authenticator app, then enter a code to switch it on. "
        "Two-factor is required for the ops dashboard; you cannot sign in without it.</p>"
        f'<p class="mono" id="totp-secret">{html.escape(secret)}</p>'
        f'<p class="mono">{html.escape(uri)}</p>'
        '<form class="addf" method="post" action="/login/enrol">'
        f'<input type="hidden" name="pending" value="{html.escape(pending)}">'
        '<div class="fld"><label>Code from your app</label>'
        '<input type="text" name="code" inputmode="numeric" autocomplete="one-time-code" required autofocus></div>'
        '<div style="margin-top:18px"><button class="submit" type="submit">Turn on two-factor and sign in</button></div>'
        "</form></div></section>"
    )
    return _page('<span class="live"><span class="dot"></span> Ops</span>', body, refresh=False)


def _render_operator_recovery_codes(codes: list[str]) -> str:
    body = (
        '<section><div class="card"><div class="card-head"><h2>Save your recovery codes</h2>'
        '<span class="count">Shown once</span></div>'
        "<p>Each code works once and signs you in if you lose your phone. They are stored "
        "hashed, so this is the only time they can be shown. If all of them are gone too, "
        "the reset is <code>sentinel operator reset-2fa</code> on the host - it is recorded.</p>"
        '<p class="mono">' + "<br>".join(html.escape(c) for c in codes) + "</p>"
        '<p style="margin-top:18px"><a class="submit" href="/">Continue to the dashboard</a></p>'
        "</div></section>"
    )
    return _page('<span class="live"><span class="dot"></span> Ops</span>', body, refresh=False)


def _unprotected_banner() -> str:
    """Shown while no operator account exists.

    Shipping operator identity must not lock an existing deployment out of its
    own dashboard, so sign-in only becomes mandatory once there is somebody to
    sign in as (G-50). Until then the page behaves exactly as it always has -
    and says so, because "the network is the only thing protecting this" is
    true either way and was previously written down nowhere the operator would
    see it."""
    return (
        '<div class="banner err">✕ <b>No operator accounts exist</b> — anyone who can reach '
        "this page can add customers and read every customer's contact details. Create one with "
        "<code>sentinel operator add &lt;username&gt;</code> and this page will require a "
        "sign-in.</div>"
    )


def _watch_threshold(rules) -> int:
    """The heartbeat threshold, from config when we have it and from the
    declared default otherwise - never a literal, so the dashboard and the
    detector cannot drift apart."""
    if rules is not None:
        return rules.get_int("detectors", "heartbeat", "consecutive_failures_threshold")
    return int(default_for("detectors", "heartbeat", "consecutive_failures_threshold") or 5)


def _rules_banner(rules) -> str:
    """Shown when config/rules.yaml on disk was rejected (G-12).

    Detection carries on with the last values known to be good, which is the
    safe behaviour but also an invisible one: every other indicator on this
    page - fleet health, last success, alert counts - stays exactly as green as
    it was. Without this the operator's only evidence that their edit never
    took effect is a log line nobody is reading."""
    if rules is None or rules.is_valid:
        return ""
    return (
        '<div class="banner err">✕ <b>config/rules.yaml was rejected</b> — detection is running on '
        "the last valid configuration, and the thresholds in the file are <b>not</b> in force. "
        f"Rejected {_ts(rules.rejected_at)}: <code>{html.escape(rules.validation_error or '')}</code></div>"
    )


def _render(db: Database, rules=None, qs=None) -> str:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()
    qs = qs or {}

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

    # #39's last criterion, server-side per the #82 decision (no table JS):
    # trouble surfaces first. The default order is by problem state -
    # trouble, then watching, then paused (deliberate stillness last) - and
    # `?state=` narrows to one bucket. An unrecognised filter value selects
    # NOTHING, never everything: showing the whole fleet under a heading
    # that claims a selection is the quiet inversion this repo keeps
    # finding elsewhere.
    threshold = _watch_threshold(rules)
    states = {int(r["id"]): _problem_state(r, threshold) for r in accounts}
    counts = {"trouble": 0, "watching": 0, "paused": 0}
    for s in states.values():
        counts[s] += 1
    state_filter = (qs.get("state") or [""])[0].strip()
    sort = (qs.get("sort") or ["problem"])[0].strip()

    shown = list(accounts)
    if state_filter:
        shown = [r for r in shown if states[int(r["id"])] == state_filter]
    if sort == "label":
        shown.sort(key=lambda r: str(r["label"]).lower())
    else:
        rank = {"trouble": 0, "watching": 1, "paused": 2}
        shown.sort(key=lambda r: (rank.get(states[int(r["id"])], 0), str(r["label"]).lower()))

    def _filter_link(value: str, label: str, n: int | None = None) -> str:
        current = " style='font-weight:700;text-decoration:underline'" if value == state_filter else ""
        suffix = f" ({n})" if n is not None else f" ({len(accounts)})"
        query = f"?state={value}" if value else "?"
        return f"<a class='acctlink'{current} href='/{query}'>{label}{suffix}</a>"

    filter_row = (
        "<p class='count' style='margin:0 0 8px'>Show: "
        + _filter_link("", "All")
        + " · " + _filter_link("trouble", "Trouble", counts["trouble"])
        + " · " + _filter_link("watching", "Watching", counts["watching"])
        + " · " + _filter_link("paused", "Paused", counts["paused"])
        + " — sorted by " + ("name" if sort == "label" else "problem state, trouble first")
        + f" (<a class='acctlink' href='/?sort={'problem' if sort == 'label' else 'label'}"
        + (f"&state={state_filter}" if state_filter else "") + "'>"
        + ("sort by problem" if sort == "label" else "sort by name") + "</a>)</p>"
    )

    account_rows = "".join(
        f"<tr><td><a class='acctlink' href='/account?id={r['id']}'>{html.escape(r['label'])} ›</a></td>"
        f"<td class='mono'>{html.escape((r['exchange'] if 'exchange' in r.keys() else 'bitget') or 'bitget')}</td>"
        f"<td class='mono'>{_account_type_label(r['topology'])}</td><td>{_status_pill(r['status'])}</td>"
        f"<td class='mono'>{_ts(r['last_success_at'])}</td>"
        f"<td>{_watch_cell(r, threshold)}</td>"
        f"<td>{_fail_cell(r['consecutive_failures'] or 0)}</td>"
        f"<td>{_fail_cell(r['consecutive_auth_failures'] or 0)}</td></tr>"
        for r in shown
    ) or ("<tr><td colspan=8 class='empty'>No accounts match this filter</td></tr>"
          if accounts else "<tr><td colspan=8 class='empty'>No accounts onboarded yet</td></tr>")

    alert_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['sent_at'])}</td>"
        f"<td class='lbl'>{html.escape(r['label'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'>{html.escape(r['rule'])}</td>"
        f"<td class='{'ack-yes' if r['acknowledged_at'] else 'ack-no'}'>{'✓' if r['acknowledged_at'] else '—'}</td></tr>"
        for r in alerts
    ) or "<tr><td colspan=5 class='empty'>No alerts — all clear</td></tr>"

    nav = (
        '<a class="live" href="/triage">Triage queue</a>'
        '<a class="live primary" href="/add">+ Add customer</a>'
        '<a class="live" href="/metrics">Metrics</a>'
        f'<span class="live"><span class="dot"></span> Live · <span id="liveclock">{now.strftime("%H:%M:%S UTC")}</span></span>'
    )
    body = (
        _unprotected_banner() if operator_count(db) == 0 else ""
    ) + (
        _rules_banner(rules)
        + '<div class="kpis">'
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
        f'<span class="count">{len(shown)} shown · {total} total</span></div>'
        + filter_row +
        '<table><thead><tr><th>Account</th><th>Exchange</th>'
        "<th>Account type</th><th>Status</th><th>Watched</th><th>Last success</th><th>Failures</th><th>Auth</th></tr></thead>"
        f"<tbody>{account_rows}</tbody></table></div></section>"
        '<section><div class="card"><div class="card-head"><h2>Recent alerts</h2>'
        '<span class="count">latest 50</span></div><table><thead><tr><th>Time</th><th>Account</th>'
        f"<th>Severity</th><th>Rule</th><th>Ack</th></tr></thead><tbody>{alert_rows}</tbody></table></div></section>"
        + _operator_pages_section(db)
        + _expected_activity_section(db)
    )
    return _page(nav, body)


def _expected_activity_section(db: Database) -> str:
    """What customers keep telling us was their own activity (G-24, #28).

    The third criterion, and the one that makes the feature worth having: a
    single "that was me" is a customer being polite about one withdrawal; the
    same rule marked repeatedly is a detector firing on how somebody trades.

    Shown with its denominator. "Marked 3 times" says nothing without "out of
    how many" - 3 of 4 is a rule to fix, 3 of 900 is a customer who once
    recognised three of their own transfers, and a bare count or a bare
    percentage would render those identically."""
    from sentinel.expected import patterns

    rows = patterns(db, limit=20)
    if not rows:
        return ""
    body = ""
    for r in rows:
        total = r["alerts_total"] or 0
        share = f"{r['marks']} of {total}" if total else f"{r['marks']}"
        # A rule whose alerts are mostly marked is the finding; flagged rather
        # than left for somebody to divide in their head.
        loud = total and r["marks"] / total >= 0.5 and r["marks"] >= 3
        flag = ' <span class="fail" title="most of this rule\'s alerts on this account were marked as expected">mostly marked</span>' if loud else ""
        body += (
            f'<tr><td class="rule">{html.escape(r["rule"])}{flag}</td>'
            f'<td>{_dd(r["label"])}</td><td class="mono">{share}</td>'
            f'<td class="mono">{_ts(r["last_marked_at"])}</td></tr>'
        )
    return (
        '<section><div class="card"><div class="card-head"><h2>Marked as expected activity</h2>'
        '<span class="count">what customers keep saying was them · top 20</span></div>'
        '<table><thead><tr><th>Rule</th><th>Account</th><th>Marked</th><th>Last</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div></section>"
    )


def _operator_pages_section(db: Database) -> str:
    """Our own faults, kept visibly apart from customer alerts (G-49).

    Separate section rather than mixed into "Recent alerts" for the same reason
    they're a separate table: a customer's alert history is something they read
    in the portal and something we hand to an exchange as evidence. Our outages
    do not belong in it, and an operator scanning this page needs to be able to
    tell "a customer may be being robbed" from "our product is broken" at a
    glance."""
    from sentinel.alerts.operator import recent_operator_pages

    pages = recent_operator_pages(db, limit=20)
    if not pages:
        return ""
    rows = ""
    for p in pages:
        about = _dd(p["account_label"]) if p["account_id"] is not None else '<span class="dim">product</span>'
        undelivered = (
            '' if p["delivered_to"] else
            ' <span class="fail" title="no channel was configured for this severity">undelivered</span>'
        )
        ack = ('<span class="ack-yes">✓</span>' if p["acknowledged_at"]
               else '<span class="ack-no">—</span>')
        rows += (
            f'<tr><td class="mono">{_ts(p["sent_at"])}</td><td>{about}</td>'
            f'<td><span class="pill sev-{html.escape(p["severity"])}">{html.escape(p["severity"])}</span></td>'
            f'<td class="rule">{html.escape(p["rule"])}{undelivered}</td><td>{ack}</td></tr>'
        )
    return (
        '<section><div class="card"><div class="card-head"><h2>Operator pages</h2>'
        '<span class="count">our faults, not the customer\'s · latest 20</span></div>'
        '<table><thead><tr><th>Time</th><th>About</th><th>Severity</th><th>Rule</th><th>Ack</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></div></section>"
    )


def _dd(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else '<span class="dim">—</span>'


def _check(ok: str, text: str, meta: str = "", meta_is_html: bool = False) -> str:
    icon = {"ok": '<span class="ic ok">✓</span>', "warn": '<span class="ic warn">!</span>',
            "na": '<span class="ic na">•</span>'}[ok]
    meta_html = f'<span class="meta">{meta if meta_is_html else html.escape(meta)}</span>' if meta else ""
    return f'<div class="check">{icon}<span class="txt">{text}</span>{meta_html}</div>'


# The contact selector names a column, so it is resolved from this fixed map
# and never interpolated. Absent means the primary, which is what the form
# posted before there was a choice to make.
PORTAL_CONTACTS = {
    "primary": ("alert_email", "Primary contact"),
    "secondary": ("alert_email_secondary", "Second contact"),
}


def _portal_access_row(db: Database, account_id: int, email: str | None, contact: str, title: str) -> str:
    """One contact's portal-credential row, with its blast radius spelled out.

    The button sits on one account's page and reads as an action on that
    account. It is not: portal identity is the email, so the credential opens
    every account that address is a contact on. That gap between what a control
    looks like it does and what it does is where mistakes come from (#61), and
    it widened when G-21 let a secondary contact hold their own credential.

    So the accounts are listed BEFORE the button, by name. Not a generic
    caution - a caution shown every time is a caution nobody reads, which is
    how the real ones get missed - but the actual list, and only when there is
    more than this account in it."""
    from sentinel.portal import member_accounts, member_exists

    if not email or not email.strip():
        return (f'<div class="det"><span class="desc">{title}: no email on file — '
                "add one to enable member-portal access.</span></div>")
    exists = member_exists(db, email)
    status = "login active" if exists else "no login yet"
    label = "Reset portal password" if exists else "Generate portal password"

    rows = member_accounts(db, email)
    reachable = [r["label"] for r in rows]
    others = [r for r in rows if r["id"] != account_id]
    # The scope belongs on the button too, not only in the paragraph above it.
    # The paragraph is read once; the button is what gets clicked, sometimes
    # months later by somebody who scrolled past the paragraph. If the control
    # is going to be pressed while thinking "this is an account A action", the
    # words under the cursor are the last chance to say otherwise (#61).
    scope = ""
    if others:
        label += f" — opens {len(reachable)} accounts"
        scope = (
            '<div class="det"><span class="desc">This credential opens '
            f'<b>{len(reachable)} account(s)</b>, not just this one: '
            + ", ".join(f"<b>{html.escape(str(lbl))}</b>" for lbl in reachable)
            + ". Portal identity is the email address, so anyone holding this password "
              "sees every account that address is a contact on.</span></div>"
        )

    return (
        f'<div class="det"><span class="desc">{title} — <b>{html.escape(email)}</b> · {status}</span></div>'
        + scope +
        f'<form method="post" action="/portal-access" style="padding:0 22px 14px">'
        f'<input type="hidden" name="account_id" value="{account_id}">'
        f'<input type="hidden" name="contact" value="{contact}">'
        f'<button class="submit" type="submit">{label}</button> '
        '<span class="hint">shown once — hand it to them securely</span></form>'
    )


def _portal_access_card(db: Database, account_id: int, email: str | None,
                        email_secondary: str | None = None) -> str:
    """Ops-side management of the member-portal logins for this customer.
    The generated password is displayed exactly once.

    Both contacts get their own row (G-21). Before this the card read only
    `alert_email`, so a second contact could be recorded and alerted and then
    never issued a password — which is half of why they couldn't sign in. They
    are separate credentials for separate people, so generating one never
    provisions the other."""
    if not (email and email.strip()) and not (email_secondary and email_secondary.strip()):
        inner = ('<div class="det"><span class="desc">No email on file — add a customer email '
                 "to enable member-portal access.</span></div>")
    else:
        inner = _portal_access_row(db, account_id, email, "primary", "Primary contact")
        if email_secondary and email_secondary.strip():
            inner += _portal_access_row(
                db, account_id, email_secondary, "secondary", "Second contact"
            )
    return ('<section><div class="card"><div class="card-head"><h2>Member portal access</h2>'
            '<span class="count">read-only member view</span></div>' + inner + "</div></section>")


def _render_triage(db: Database, rules=None, show_handled: bool = False) -> str:
    """G-37 (#41): the queue of alerts nobody has answered, across every
    customer.

    Until this page, alerts were only visible per account, so working the
    backlog meant knowing which pages to open - and an alert nobody opens
    looks identical to an alert nobody needed to. This is the other side of
    the portal status page: that one lets a customer see they are being
    watched, this one makes sure somebody at Minas is doing the watching.

    "Handled" here is Minas-side bookkeeping and never touches
    `acknowledged_at` - see sentinel/triage.py for why an operator must not be
    able to stop a customer's escalation ladder by tidying their queue.
    """
    from sentinel.alert_triage import muted_channels, open_alerts, stale_after_minutes

    queue = open_alerts(db, rules, include_handled=show_handled)
    muted = muted_channels(db)
    overdue_after = stale_after_minutes(rules)

    rows = ""
    for a in queue:
        age = "\u2014" if a["age_minutes"] is None else (
            f"{a['age_minutes']}m" if a["age_minutes"] < 120 else f"{a['age_minutes'] // 60}h"
        )
        age_cell = (f'<td class="mono" style="color:var(--bad);font-weight:600">{age} \u26a0</td>'
                    if a["overdue"] else f'<td class="mono">{age}</td>')
        flags = []
        if a["delivered_to"] == 0:
            flags.append("delivered to nobody")
        if a["escalated"]:
            flags.append("escalated")
        if a["handled"]:
            flags.append(f"handled by {html.escape(str(a['handled_by'] or '?'))}")
        note = html.escape(a["note"][:80]) if a["note"] else ""
        rows += (
            f"<tr><td class='mono'>{_ts(a['sent_at'])}</td>"
            f"<td class='lbl'><a class='acctlink' href='/account?id={a['account_id']}'>"
            f"{html.escape(a['label'])} \u203a</a></td>"
            f"<td><span class='pill sev-{a['severity']}'>{a['severity']}</span></td>"
            f"<td class='rule'>{html.escape(a['rule'])}</td>"
            + age_cell +
            f"<td class='mono'>{html.escape(', '.join(flags))}</td>"
            "<td>"
            f"<form method='post' action='/triage' style='display:flex;gap:6px;margin:0'>"
            f"<input type='hidden' name='alert_id' value='{a['alert_id']}'>"
            f"<input type='text' name='note' value='{note}' placeholder='note' "
            "style='width:150px;background:var(--bg);color:inherit;border:1px solid var(--line);"
            "border-radius:4px;padding:3px 6px;font-size:12px'>"
            + ("<button class='submit' name='act' value='unhandle' type='submit'>Reopen</button>"
               if a["handled"] else
               "<button class='submit' name='act' value='handle' type='submit'>Mark handled</button>")
            + "</form></td></tr>"
        )
    if not rows:
        rows = "<tr><td colspan=7 class='empty'>Nothing waiting \u2014 every alert is acknowledged</td></tr>"

    muted_rows = "".join(
        f"<tr><td class='lbl'><a class='acctlink' href='/account?id={m['account_id']}'>"
        f"{html.escape(m['label'])} \u203a</a></td>"
        f"<td>{html.escape(', '.join(m['snoozed_rules']) or chr(0x2014))}</td>"
        f"<td>{'SMS off' if m['sms_off'] else chr(0x2014)}</td></tr>"
        for m in muted
    ) or "<tr><td colspan=3 class='empty'>Nobody is muting anything</td></tr>"

    toggle = ('<a class="acctlink" href="/triage">hide handled \u203a</a>' if show_handled
              else '<a class="acctlink" href="/triage?handled=1">show handled too \u203a</a>')

    body = (
        '<section><div class="card"><div class="card-head"><h2>Unacknowledged alerts \u2014 all customers</h2>'
        f'<span class="count">{len(queue)} open \u00b7 \u26a0 after {overdue_after} min \u00b7 {toggle}</span></div>'
        "<table><thead><tr><th>Sent</th><th>Account</th><th>Severity</th><th>Rule</th>"
        "<th>Age</th><th>Flags</th><th>Triage</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
        '<section><div class="card"><div class="card-head"><h2>Who is muting what</h2>'
        '<span class="count">the early warning for a noise problem</span></div>'
        "<table><thead><tr><th>Account</th><th>Snoozed rules (live)</th><th>Channels</th></tr></thead>"
        f"<tbody>{muted_rows}</tbody></table></div></section>"
    )
    nav = '<a class="live" href="/">\u2190 Dashboard</a>'
    return _page(nav, body)


def _render_account(db: Database, account_id: int, notice_html: str = "") -> str | None:
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

    # Member SMS notifications: nudge the operator when there is no phone to
    # text (a CRITICAL alert would have nowhere to page), and surface an
    # explicit opt-out so nobody wonders why a member got no text.
    has_phone = bool(f("alert_phone") or f("alert_phone_secondary"))
    sms_pref = f("sms_alerts_enabled")
    sms_on = True if sms_pref is None else bool(sms_pref)
    if not has_phone:
        sms_meta = "no phone number on file — add or verify one to enable text alerts"
    elif not sms_on:
        sms_meta = "member opted out — CRITICAL alerts may still page per business rules"
    else:
        sms_meta = ", ".join(p for p in (f("alert_phone"), f("alert_phone_secondary")) if p)
    checks += _check(
        "ok" if (has_phone and sms_on) else "warn",
        "<b>SMS text alerts</b> — urgent alerts page the member by text (ClickSend)",
        sms_meta,
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
        notice_html
        + '<section><div class="card"><div class="card-head">'
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
        + _portal_access_card(db, account_id, f("alert_email"), f("alert_email_secondary"))
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


def serve_dashboard(
    db: Database, cipher: CredentialCipher, port: int, host: str = "127.0.0.1", rules=None,
    operator_alerter=None,
) -> ThreadingHTTPServer:
    """Internal ops page. Network-gated (Tailscale) — it has no login of its
    own by design. The /add form runs the same fail-closed read-only gate as
    the CLI, so a trade/withdraw key can never be stored through it.

    `host` defaults to loopback because this page has no login: on any other
    default, following the quickstart puts an unauthenticated "add customer"
    form — and every customer's name, email and phone — on every interface of
    whatever machine it runs on (G-36). Widening it is a deliberate act:
    `SENTINEL_DASHBOARD_HOST`, and only behind a VPN, an SSH tunnel or an
    authenticating proxy. In Docker the container sets `0.0.0.0` and the
    loopback *publish* in docker-compose.yml is what gates it instead."""

    # Shared across requests for this server, like the portal's. Throttles by
    # client IP and by username, so neither a single source nor a single
    # account can be ground down.
    _login_throttle = LoginThrottle()

    def _same_origin(handler) -> bool:
        """Is this POST from our own pages? (CSRF gate on every state change.)

        Sec-Fetch-Site is the PRIMARY signal: the browser sets it and a page
        cannot forge it (a forbidden header). `same-origin` is our own form;
        `none` is user-initiated (address bar / bookmark); anything else is
        another site posting at us. The Origin comparison is only the
        fallback for clients that do not send Sec-Fetch-Site.

        `Origin: null` alone is NEVER accepted: sandboxed iframes and some
        cross-site navigations serialise null too, and under a no-referrer
        policy even our OWN form posts did - which is how this check rejected
        every real browser sign-in while curl (no referrer policy) and the
        test client (no Origin at all) sailed through. Two correct hardening
        measures colliding; the referrer policy is now `same-origin` so
        Origin stays populated, and this check no longer depends on it alone.
        """
        fetch_site = (handler.headers.get("Sec-Fetch-Site") or "").lower()
        if fetch_site:
            return fetch_site in ("same-origin", "none")
        origin = handler.headers.get("Origin")
        if not origin:
            return True  # non-browser clients send neither header
        return urlparse(origin).netloc == (handler.headers.get("Host") or "")

    class Handler(BaseHTTPRequestHandler):
        def finish(self):
            # This thread dies when the connection does; its DB connection has
            # to die with it, or every request leaks a file descriptor until
            # the process cannot open any socket - alerts included (#79).
            try:
                super().finish()
            finally:
                db.release_thread_connection()

        def _send(self, body: bytes, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # same-origin, matching the portal - and deliberately NOT
            # no-referrer, which serialises Origin as null on our own form
            # posts and broke the portal's CSRF gate for every browser. The
            # dashboard never set a policy, which is the only reason its
            # POSTs kept working; now the choice is explicit instead of an
            # accident of omission.
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(body)

        def _operator(self) -> str | None:
            """The signed-in operator, or None.

            Re-derived per request rather than trusted from the token, so
            disabling an account takes effect immediately instead of at the end
            of a 12-hour session."""
            raw = self.headers.get("Cookie") or ""
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE:
                    return session_operator(db, value)
            return None

        def _gate(self) -> bool:
            """True when the request may proceed.

            Sign-in is required only once an operator account exists. That is
            the upgrade path: shipping this must not lock an existing
            deployment out of its own dashboard, which is the same mistake as
            refusing to start on a config a previous build accepted. Until
            somebody creates an account the page behaves as it always has, and
            says loudly that it is unprotected."""
            if operator_count(db) == 0 or self._operator() is not None:
                return True
            self._send(_render_operator_login().encode(), 401)
            return False

        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/login":
                self._send(_render_operator_login().encode())
                return
            if path == "/logout":
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header(
                    "Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not self._gate():
                return
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
            elif path == "/triage":
                qs = parse_qs(urlparse(self.path).query)
                show_handled = qs.get("handled", ["0"])[0] == "1"
                self._send(_render_triage(db, rules, show_handled).encode())
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
                self._send(_render(db, rules, parse_qs(urlparse(self.path).query)).encode())

        def do_POST(self):
            # One chokepoint for every POST, /login included (the portal's
            # pattern): a cross-site login POST is login-CSRF, and gating
            # only the action routes left it open here - found by #92's
            # refusal tests. The per-handler checks below remain as depth.
            if not _same_origin(self):
                self._send(b"cross-origin POST rejected", 403)
                return
            post_path = urlparse(self.path).path.rstrip("/")
            if post_path == "/login":
                self._login()
                return
            if post_path == "/login/code":
                self._login_code()
                return
            if post_path == "/login/enrol":
                self._login_enrol()
                return
            if not self._gate():
                return
            if post_path == "/portal-access":
                self._portal_access()
                return
            if post_path == "/triage":
                self._triage()
                return
            if post_path != "/add":
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
                    db, cipher, actor=self._operator(),
                    operator_alerter=operator_alerter,
                    label=label, topology=g("topology") or "independent",
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

        def _triage(self):
            """Note + handled marker for one alert (G-37, #41).

            Same-origin like every other state-changing POST here, and the
            actor comes from the session. Writes through
            `sentinel.alert_triage.set_triage`, which cannot touch
            `acknowledged_at` - the escalation ladder is the customer's, not
            the operator's to tidy away.
            """
            if not _same_origin(self):
                self._send(b"cross-origin POST rejected", 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 16 * 1024:
                self._send(b"bad request", 400)
                return
            form = parse_qs(self.rfile.read(length).decode())
            try:
                alert_id = int(form.get("alert_id", ["0"])[0])
            except ValueError:
                alert_id = 0
            act = form.get("act", [""])[0]
            note = form.get("note", [None])[0]

            from sentinel.alert_triage import set_triage

            handled = {"handle": True, "unhandle": False}.get(act)
            try:
                set_triage(db, alert_id, note=note, handled=handled,
                           actor=self._operator())
            except ValueError:
                self._send(b"no such alert", 404)
                return
            self.send_response(303)
            self.send_header("Location", "/triage")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _portal_access(self):
            """Generate/reset the member-portal password for an account's
            customer email; the password is rendered exactly once."""
            if not _same_origin(self):
                self._send(b"cross-origin POST rejected", 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 4 * 1024:
                self._send(b"bad request", 400)
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            try:
                aid = int((form.get("account_id", ["0"])[0]))
            except ValueError:
                aid = 0
            # Which contact this password is for. Absent means the primary,
            # which is what the form posted before there was a choice. Unknown
            # values are refused rather than defaulted: the value selects a
            # column, and quietly falling back to the primary would hand one
            # person a credential meant for another.
            contact = (form.get("contact", ["primary"])[0] or "primary").strip()
            if contact not in PORTAL_CONTACTS:
                self._send(b"unknown contact", 400)
                return
            column, title = PORTAL_CONTACTS[contact]

            with db.cursor() as cur:
                cur.execute(f"SELECT {column} AS contact_email FROM accounts WHERE id = ?", (aid,))
                row = cur.fetchone()
            email = (row["contact_email"] if row else None) or ""
            if not email.strip():
                self._send(f"account not found or has no {title.lower()} email".encode(), 404)
                return

            import secrets as _secrets

            from sentinel.portal import member_accounts, set_member_password

            password = _secrets.token_urlsafe(12)
            set_member_password(db, email, password)
            actor = self._operator()
            # What this credential opens, resolved at the moment it is issued.
            # The event below is a forensic record, and the question asked of
            # it later is "what did this password reach", which the email alone
            # cannot answer: contacts get added and removed, so replaying
            # `member_accounts` next month gives a different set than the one
            # actually handed over (#61).
            granted = member_accounts(db, email)
            granted_labels = [str(r["label"]) for r in granted]
            logger.info(
                "portal password generated for account %d (%s contact) by %s - opens %d account(s): %s",
                aid, contact, actor or "an unattributed operator",
                len(granted_labels), ", ".join(granted_labels),
            )
            # Handing someone a credential is an act with a person behind it,
            # and it now leaves a row naming them (G-50).
            append_event(
                db, aid, "member_portal_password_generated", "LOW",
                {
                    "contact": contact,
                    "email": email.strip().lower(),
                    "granted_account_ids": [int(r["id"]) for r in granted],
                    "granted_account_labels": granted_labels,
                },
                actor=actor,
            )
            # The confirmation says what was handed over, not just to whom. The
            # operator is about to send this password to somebody; if the blast
            # radius is going to surprise them, this is the last moment it can.
            scope_note = ""
            if len(granted_labels) > 1:
                scope_note = (
                    " This password opens <b>"
                    + html.escape(", ".join(granted_labels))
                    + "</b>."
                )
            notice = (
                f'<div class="banner ok">✓ Member portal password — {html.escape(title)}, '
                f"<b>{html.escape(email.strip().lower())}</b>: <code>{html.escape(password)}</code>"
                " — copy it now, it is shown only once." + scope_note + "</div>"
            )
            self._send(_render_account(db, aid, notice_html=notice).encode())

        def _login(self):
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 4 * 1024:
                self._send(_render_operator_login("empty or oversized submission").encode(), 400)
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            username = (form.get("username", [""])[0] or "").strip()
            password = form.get("password", [""])[0] or ""

            client_ip = self.client_address[0] if self.client_address else "?"
            if _login_throttle.blocked(f"ip:{client_ip}") or _login_throttle.blocked(f"user:{username.lower()}"):
                logger.warning("operator login throttled for %s from %s", username, client_ip)
                self._send(_render_operator_login("too many attempts - wait and try again").encode(), 429)
                return

            if not check_operator_login(db, username, password):
                _login_throttle.record_failure(f"ip:{client_ip}")
                _login_throttle.record_failure(f"user:{username.lower()}")
                logger.warning("failed operator login for %r from %s", username, client_ip)
                # One message for a bad username and a bad password alike, so
                # the form is not an oracle for which accounts exist.
                self._send(_render_operator_login("invalid username or password").encode(), 401)
                return

            _login_throttle.reset(f"ip:{client_ip}")
            _login_throttle.reset(f"user:{username.lower()}")
            # The password alone mints NO session (#87, decision 19). It
            # mints a five-minute pending token, and the second step - a
            # code, or first-login enrolment - is what signs in.
            if cipher is None:
                # Without the master key there is no way to read a stored
                # secret or store a new one. Refuse rather than fall back to
                # password-only: a dashboard that is sometimes two-factor is
                # a dashboard that is not.
                logger.error("operator %s: two-factor cannot be checked - no master key", username.lower())
                self._send(_render_operator_login("two-factor is unavailable on this server - "
                                                  "SENTINEL_MASTER_KEY is not set").encode(), 503)
                return
            from sentinel.twofactor import OPERATOR, begin_enrolment, is_enabled

            pending = make_pending_token(session_secret(db), username)
            if is_enabled(db, OPERATOR, username):
                logger.info("operator %s passed the password step from %s", username.lower(), client_ip)
                self._send(_render_operator_code(pending).encode())
                return
            secret, uri = begin_enrolment(db, cipher, OPERATOR, username)
            logger.info("operator %s has no second factor - enrolment required (from %s)",
                        username.lower(), client_ip)
            self._send(_render_operator_enrol(pending, secret, uri).encode())

        def _pending_form(self):
            """(username, code, form) for the second-step POSTs, or None after
            an error response has been sent."""
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 4 * 1024:
                self._send(_render_operator_login("empty or oversized submission").encode(), 400)
                return None
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            pending = form.get("pending", [""])[0] or ""
            username = verify_pending_token(session_secret(db), pending)
            if username is None:
                # Expired, forged, or a session cookie presented in its place.
                logger.warning("operator second step with an invalid pending token from %s",
                               self.client_address[0] if self.client_address else "?")
                self._send(_render_operator_login("that sign-in expired - start again").encode(), 401)
                return None
            return username, (form.get("code", [""])[0] or "").strip(), pending

        def _issue_session(self, username: str, body: bytes | None = None) -> None:
            token = make_session_token(session_secret(db), username)
            cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200"
            if body is None:
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _login_code(self):
            parsed = self._pending_form()
            if parsed is None:
                return
            username, code, pending = parsed
            client_ip = self.client_address[0] if self.client_address else "?"
            if _login_throttle.blocked(f"ip:{client_ip}") or _login_throttle.blocked(f"user:{username}"):
                logger.warning("operator second factor throttled for %s from %s", username, client_ip)
                self._send(_render_operator_code(pending, "too many attempts - wait and try again").encode(), 429)
                return
            from sentinel.twofactor import OPERATOR, check_second_factor

            if cipher is None or not check_second_factor(db, cipher, OPERATOR, username, code):
                # Counted on the same limiter as passwords: six digits is a
                # million guesses, and a code endpoint that accepts them as
                # fast as they arrive is not a second factor.
                _login_throttle.record_failure(f"ip:{client_ip}")
                _login_throttle.record_failure(f"user:{username}")
                logger.warning("operator second factor rejected for %s from %s", username, client_ip)
                self._send(_render_operator_code(pending, "that code was not accepted").encode(), 401)
                return
            _login_throttle.reset(f"ip:{client_ip}")
            _login_throttle.reset(f"user:{username}")
            logger.info("operator %s signed in from %s", username, client_ip)
            self._issue_session(username)

        def _login_enrol(self):
            parsed = self._pending_form()
            if parsed is None:
                return
            username, code, pending = parsed
            client_ip = self.client_address[0] if self.client_address else "?"
            from sentinel.totp import provisioning_uri
            from sentinel.twofactor import OPERATOR, confirm_enrolment, pending_secret

            if cipher is None:
                self._send(_render_operator_login("two-factor is unavailable on this server").encode(), 503)
                return
            codes = confirm_enrolment(db, cipher, OPERATOR, username, code)
            if codes is None:
                secret = pending_secret(db, cipher, OPERATOR, username)
                if secret is None:
                    # No unconfirmed enrolment to confirm: start over.
                    self._send(_render_operator_login("start the sign-in again").encode(), 401)
                    return
                self._send(_render_operator_enrol(pending, secret, provisioning_uri(secret, username),
                                                  "that code was not accepted - nothing has changed").encode(), 401)
                return
            record_operator_event(db, "two_factor_enrolled", username, actor=username,
                                  detail=f"first-login enrolment from {client_ip}")
            logger.info("operator %s enrolled a second factor and signed in from %s", username, client_ip)
            self._issue_session(username, _render_operator_recovery_codes(codes).encode())

        def log_message(self, *args):  # keep stdout JSON-clean
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    return server
