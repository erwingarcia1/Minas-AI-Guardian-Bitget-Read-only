from __future__ import annotations

import html as html_mod
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("sentinel.metrics")

try:
    from prometheus_client import REGISTRY, Counter, Gauge, generate_latest
    from prometheus_client.exposition import CONTENT_TYPE_LATEST

    _AVAILABLE = True
except ImportError:  # metrics are strictly optional - never a hard dependency at runtime
    _AVAILABLE = False


class Metrics:
    """Prometheus counters/gauges. A no-op shell when prometheus_client
    isn't installed, so callers never need to null-check."""

    def __init__(self, registry=None):
        # Maintained regardless of _AVAILABLE - see untimestamped_record.
        self._undated: dict = {}
        self._undated_lock = threading.Lock()
        if not _AVAILABLE:
            return
        reg = REGISTRY if registry is None else registry
        self.polls_ok = Counter("sentinel_polls_ok_total", "Successful poll cycles", registry=reg)
        self.polls_failed = Counter("sentinel_polls_failed_total", "Failed poll cycles", ["kind"], registry=reg)
        self.detections = Counter(
            "sentinel_detections_total", "Detections fired", ["rule", "severity"], registry=reg
        )
        self.alerts = Counter("sentinel_alerts_sent_total", "Alerts sent (post-dedup)", ["severity"], registry=reg)
        self.accounts_active = Gauge("sentinel_accounts_active", "Accounts with status=active", registry=reg)
        # A record the exchange sent without a usable timestamp. Rising here
        # means an exchange changed its response format: those records can no
        # longer be placed in time, so quantity-drop stops crediting them as
        # explanations (G-7) and off-market/fee checking stops seeing them at
        # all (#51). Both failures are silent, which is exactly why this is a
        # counter an operator can alarm on and not only a log line.
        self.untimestamped_records = Counter(
            "sentinel_untimestamped_records_total",
            "Exchange records received with a missing or unparseable timestamp",
            ["exchange", "source"],
            registry=reg,
        )
        # 0 means config/rules.yaml on disk was rejected and detection is
        # running on the last values known to be good (G-12). Its resting value
        # is 1, so this is a flat line an operator can alarm on: the thresholds
        # someone just edited are not the thresholds in force, and every other
        # indicator - poll success, account health, alert volume - looks
        # completely normal while that is true.
        self.rules_config_valid = Gauge(
            "sentinel_rules_config_valid",
            "1 when config/rules.yaml validated and is in use, 0 when it was rejected",
            registry=reg,
        )
        self.rules_config_valid.set(1)

    def set_rules_config_valid(self, ok: bool) -> None:
        if _AVAILABLE:
            self.rules_config_valid.set(1 if ok else 0)

    def poll_ok(self) -> None:
        if _AVAILABLE:
            self.polls_ok.inc()

    def poll_failed(self, auth: bool) -> None:
        if _AVAILABLE:
            self.polls_failed.labels(kind="auth" if auth else "generic").inc()

    def detection_fired(self, rule: str, severity: str) -> None:
        if _AVAILABLE:
            self.detections.labels(rule=rule, severity=severity).inc()

    def alert_sent(self, severity: str) -> None:
        if _AVAILABLE:
            self.alerts.labels(severity=severity).inc()

    def set_active_accounts(self, count: int) -> None:
        if _AVAILABLE:
            self.accounts_active.set(count)

    def untimestamped_record(self, exchange: str, source: str) -> None:
        # Counted twice on purpose. The Prometheus counter is for scraping; the
        # plain dict is what `alarms.UntimestampedRecordAlarm` reads, and it is
        # maintained whether or not prometheus_client is installed. This class
        # is explicitly a no-op shell without it, and an alarm that quietly
        # stops existing along with an optional dependency is worse than no
        # alarm - it looks like coverage.
        with self._undated_lock:
            self._undated[(exchange, source)] = self._undated.get((exchange, source), 0) + 1
        if _AVAILABLE:
            self.untimestamped_records.labels(exchange=exchange, source=source).inc()

    def untimestamped_snapshot(self) -> dict:
        """Cumulative undated-record counts per (exchange, source). A copy, so
        a caller diffing two snapshots isn't reading a live dict."""
        with self._undated_lock:
            return dict(self._undated)

    @staticmethod
    def serve(port: int, host: str = "127.0.0.1"):
        """Loopback by default, on the same terms as the dashboard and portal
        (G-36). The exposition is unauthenticated and the styled view leaks
        operational shape - account counts, which rules are firing, which
        exchanges are misbehaving - so it is reconnaissance for anyone who can
        reach it. `SENTINEL_METRICS_HOST` widens it; in Docker the container
        sets 0.0.0.0 and the loopback publish is the gate instead.

        Returns the server (None when prometheus_client isn't installed) so
        the bind can be asserted."""
        if not _AVAILABLE:
            logger.warning("prometheus_client not installed - metrics endpoint disabled")
            return None
        server = _serve_metrics(port, host)
        logger.info(
            "prometheus metrics on %s:%d (raw for scrapers, styled for browsers)", host, port
        )
        return server


# --- browser-facing styled view ------------------------------------------------

_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minas AI Guardian · Metrics</title>
<meta http-equiv="refresh" content="15">
<style>
  :root {{
    --bg: #0b0813; --panel: #140f20; --panel-2: #191229;
    --line: #2b2140; --line-soft: #211935;
    --txt: #ece8f4; --muted: #9a8fb5; --faint: #6b5f8a;
    --accent: #8b5cf6; --gold: #f0b429; --ok: #34d399;
    --crit: #ff5d6c; --high: #ffa63d; --med: #ffd23d; --low: #8a7fa5;
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
    -webkit-font-smoothing: antialiased; letter-spacing: .1px; min-height: 100vh;
  }}
  .wrap {{ max-width: 1120px; margin: 0 auto; padding: 40px 32px 64px; }}
  header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 34px; }}
  .brand {{ display: flex; align-items: center; gap: 14px; }}
  .mark {{ width: 40px; height: 40px; border-radius: 12px;
    background: linear-gradient(145deg, #1c1230, #0d0918);
    border: 1px solid rgba(139,92,246,.45);
    display: grid; place-items: center;
    box-shadow: 0 6px 22px rgba(139,92,246,.30), inset 0 1px 0 rgba(255,255,255,.08); }}
  .mark svg {{ width: 24px; height: 24px; }}
  .brand h1 {{ font-size: 19px; font-weight: 650; margin: 0; letter-spacing: .2px; }}
  .brand h1 .minas {{ color: var(--gold); font-weight: 780; letter-spacing: 3px; }}
  .brand .sub {{ font-size: 12px; color: var(--faint); margin-top: 2px; }}
  .nav {{ display: flex; align-items: center; gap: 10px; }}
  .chip {{ display: inline-flex; align-items: center; gap: 9px; font-size: 12.5px; color: var(--muted);
           background: var(--panel); border: 1px solid var(--line); padding: 8px 14px;
           border-radius: 999px; text-decoration: none; }}
  a.chip:hover {{ border-color: var(--accent); color: var(--txt); }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--ok);
          box-shadow: 0 0 0 0 rgba(52,211,153,.6); animation: pulse 2.2s infinite; }}
  @keyframes pulse {{
    0% {{ box-shadow: 0 0 0 0 rgba(52,211,153,.55); }}
    70% {{ box-shadow: 0 0 0 8px rgba(52,211,153,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(52,211,153,0); }}
  }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
  .metric {{
    background: linear-gradient(180deg, var(--panel-2), var(--panel));
    border: 1px solid var(--line); border-radius: 16px; padding: 18px 20px;
    position: relative; overflow: hidden;
  }}
  .metric::after {{ content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
    background: linear-gradient(90deg, transparent, rgba(139,92,246,.6), rgba(240,180,41,.5), transparent); opacity: .6; }}
  .metric .name {{ font-size: 11.5px; text-transform: uppercase; letter-spacing: .8px; color: var(--faint);
                   display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
  .tag {{ font-size: 10px; padding: 2px 8px; border-radius: 999px; letter-spacing: .5px;
          background: rgba(139,92,246,.14); color: #b79bff; text-transform: lowercase; }}
  .metric .val {{ font-size: 32px; font-weight: 680; margin-top: 10px; line-height: 1;
                  font-variant-numeric: tabular-nums; }}
  .metric .doc {{ font-size: 12px; color: var(--muted); margin-top: 8px; }}
  .rows {{ margin-top: 12px; display: flex; flex-direction: column; gap: 7px; }}
  .row {{ display: flex; justify-content: space-between; align-items: center; gap: 10px;
          padding: 7px 10px; background: rgba(11,8,19,.5); border: 1px solid var(--line-soft);
          border-radius: 10px; }}
  .row .v {{ font-variant-numeric: tabular-nums; font-weight: 640; font-size: 14px; }}
  .pills {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .pill {{ display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 600;
           padding: 3px 9px; border-radius: 999px; letter-spacing: .3px;
           background: rgba(124,138,165,.14); color: var(--muted); }}
  .pill::before {{ content: ""; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }}
  .sev-CRITICAL {{ color: var(--crit); background: rgba(255,93,108,.13); }}
  .sev-HIGH {{ color: var(--high); background: rgba(255,166,61,.13); }}
  .sev-MEDIUM {{ color: var(--med); background: rgba(255,210,61,.13); }}
  .sev-LOW {{ color: var(--low); background: rgba(124,138,165,.15); }}
  section {{ margin-top: 30px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; }}
  .card-head {{ display: flex; align-items: baseline; justify-content: space-between;
                padding: 16px 22px; border-bottom: 1px solid var(--line-soft); }}
  .card-head h2 {{ font-size: 14px; font-weight: 620; margin: 0; letter-spacing: .3px; }}
  .card-head .count {{ font-size: 12px; color: var(--faint); }}
  table {{ width: 100%; border-collapse: collapse; }}
  thead th {{ font-size: 11px; text-transform: uppercase; letter-spacing: .7px; color: var(--faint);
              text-align: left; padding: 12px 22px; font-weight: 600; }}
  tbody td {{ padding: 12px 22px; font-size: 13px; border-top: 1px solid var(--line-soft); }}
  tbody tr:hover {{ background: rgba(139,92,246,.05); }}
  .mono {{ font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, monospace;
           font-size: 12.5px; color: var(--muted); font-variant-numeric: tabular-nums; }}
  .num {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
  .empty {{ padding: 30px; text-align: center; color: var(--faint); font-size: 13px; }}
  footer {{ margin-top: 28px; text-align: center; color: var(--faint); font-size: 11.5px; }}
  @media (max-width: 860px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
  @media (max-width: 620px) {{ .grid {{ grid-template-columns: 1fr; }} .wrap {{ padding: 24px 16px; }} }}
</style></head><body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="mark"><svg viewBox="0 0 24 24" fill="none"><defs><linearGradient id="inf" x1="3" y1="20" x2="21" y2="4" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#f0b429"/><stop offset=".48" stop-color="#f0b429"/><stop offset=".58" stop-color="#8b5cf6"/><stop offset="1" stop-color="#6d28d9"/></linearGradient></defs><path fill="url(#inf)" d="M18.6 6.62c-1.44 0-2.8.56-3.77 1.53L12 10.66 10.48 12h.01L7.8 14.39c-.64.64-1.49.99-2.4.99-1.87 0-3.39-1.51-3.39-3.38S3.53 8.62 5.4 8.62c.91 0 1.76.35 2.44 1.03l1.13 1 1.51-1.34L9.22 8.2C8.2 7.18 6.84 6.62 5.4 6.62 2.42 6.62 0 9.04 0 12s2.42 5.38 5.4 5.38c1.44 0 2.8-.56 3.77-1.53l2.83-2.5.01.01L13.52 12h-.01l2.69-2.39c.64-.64 1.49-.99 2.4-.99 1.87 0 3.39 1.51 3.39 3.38s-1.52 3.38-3.39 3.38c-.9 0-1.76-.35-2.44-1.03l-1.14-1.01-1.51 1.34 1.27 1.12c1.02 1.01 2.37 1.57 3.82 1.57 2.98 0 5.4-2.41 5.4-5.38s-2.42-5.38-5.4-5.38z"/></svg></div>
      <div><h1><span class="minas">MINAS</span> AI Guardian · Metrics</h1><div class="sub">Service telemetry — Prometheus exposition</div></div>
    </div>
    <div class="nav">
      <a class="chip" href="/">Dashboard</a>
      <a class="chip" href="/metrics/raw">Raw</a>
      <span class="chip"><span class="dot"></span> Live · {now}</span>
    </div>
  </header>

  <div class="grid">{service_cards}</div>

  <section>
    <div class="card">
      <div class="card-head"><h2>Runtime</h2><span class="count">python / process</span></div>
      <table>
        <thead><tr><th>Metric</th><th>Labels</th><th>Value</th></tr></thead>
        <tbody>{runtime_rows}</tbody>
      </table>
    </div>
  </section>

  <footer>Minas AI Guardian · scrapers get the raw exposition format automatically · refreshes every 15s</footer>
</div>
</body></html>"""

_TYPE_TAGS = {"counter": "counter", "gauge": "gauge", "histogram": "histogram", "summary": "summary"}


def _fmt(value: float, name: str = "") -> str:
    if name.endswith("_bytes"):
        v = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if v < 1024 or unit == "TB":
                return f"{v:,.1f} {unit}" if unit != "B" else f"{int(v)} B"
            v /= 1024
    if name.endswith("_seconds") and value > 1e9:  # unix timestamp gauges
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.3f}"


def _label_pills(labels: dict) -> str:
    pills = []
    for k, v in labels.items():
        cls = f" sev-{v}" if k == "severity" else ""
        pills.append(f'<span class="pill{cls}">{html_mod.escape(k)}: {html_mod.escape(str(v))}</span>')
    return f'<span class="pills">{"".join(pills)}</span>' if pills else '<span class="mono">—</span>'


def render_metrics_html(dash_port: int = 8390) -> str:
    """Premium HTML view of the live Prometheus registry, themed to match
    the status dashboard. Pure presentation - the registry stays the single
    source of truth and scrapers never see this."""
    service_cards: list[str] = []
    runtime_rows: list[str] = []

    for family in REGISTRY.collect():
        samples = [s for s in family.samples if not s.name.endswith("_created")]
        if not samples:
            continue
        tag = _TYPE_TAGS.get(family.type, family.type)

        if family.name.startswith("sentinel_"):
            pretty = family.name.removeprefix("sentinel_").replace("_", " ")
            doc = html_mod.escape(family.documentation or "")
            labeled = [s for s in samples if s.labels]
            unlabeled = [s for s in samples if not s.labels]

            body = ""
            if unlabeled:
                body += f'<div class="val">{_fmt(unlabeled[0].value, unlabeled[0].name)}</div>'
            if labeled:
                total = sum(s.value for s in labeled)
                if not unlabeled:
                    body += f'<div class="val">{_fmt(total)}</div>'
                rows = "".join(
                    f'<div class="row">{_label_pills(s.labels)}<span class="v">{_fmt(s.value, s.name)}</span></div>'
                    for s in sorted(labeled, key=lambda s: -s.value)
                )
                body += f'<div class="rows">{rows}</div>'
            if not labeled and not unlabeled:
                body += '<div class="val">0</div>'

            service_cards.append(
                f'<div class="metric"><div class="name"><span>{html_mod.escape(pretty)}</span>'
                f'<span class="tag">{tag}</span></div>{body}<div class="doc">{doc}</div></div>'
            )
        else:
            for s in samples:
                runtime_rows.append(
                    f'<tr><td class="mono">{html_mod.escape(s.name)}</td>'
                    f"<td>{_label_pills(s.labels)}</td>"
                    f'<td class="num">{_fmt(s.value, s.name)}</td></tr>'
                )

    return _PAGE.format(
        now=datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        service_cards="".join(service_cards) or '<div class="empty">No service metrics yet</div>',
        runtime_rows="".join(runtime_rows) or '<tr><td colspan="3" class="empty">none</td></tr>',
    )


def raw_exposition() -> tuple[str, bytes]:
    """Raw Prometheus exposition (content-type, body) - what scrapers consume.
    Served same-origin at /metrics/raw so the browser 'Raw' link works behind
    the Tailscale HTTPS proxy (which only fronts port 443)."""
    if not _AVAILABLE:
        return "text/plain; charset=utf-8", b"# prometheus_client not installed\n"
    return CONTENT_TYPE_LATEST, generate_latest(REGISTRY)


def _serve_metrics(port: int, host: str = "127.0.0.1"):
    """Content-negotiating metrics server: browsers (Accept: text/html) get
    the styled view; Prometheus scrapers get the raw exposition format they
    require. /raw always returns the raw format."""
    import os
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    dash_port = int(os.environ.get("SENTINEL_DASHBOARD_PORT", "8390"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            wants_html = "text/html" in (self.headers.get("Accept") or "")
            if self.path != "/raw" and wants_html:
                body = render_metrics_html(dash_port).encode()
                content_type = "text/html; charset=utf-8"
            else:
                body = generate_latest(REGISTRY)
                content_type = CONTENT_TYPE_LATEST
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep stdout JSON-clean
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="metrics").start()
    return server
