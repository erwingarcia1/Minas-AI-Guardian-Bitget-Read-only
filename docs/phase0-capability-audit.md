# Sentinel — Phase 0 Capability Audit

Status: **Phase 1 MVP in progress, live-tested against a real read-only key**
Date: 2026-07-02 (addendum 2026-07-02)
Scope: verify, against live sources, which signals in the Detection Catalog (§4 of the brief) are actually obtainable from CCXT + Bitget, for both account topologies (A: sub-account/broker, B: independent customer keys).

Sources are cited inline. Anything not directly confirmed against a live source is marked **UNVERIFIED** — none of the endpoints, fields, or limits below were guessed from training-data memory of "what exchanges usually expose."

---

## 0. Topology decision context

You confirmed **both topologies at launch**:
- **(A) Sub-account model** — customers are sub-accounts under our Bitget broker/main account.
- **(B) Independent model** — customers hold their own Bitget account and grant us a read-only key.

This matters more than it looks: **topology A gives us strictly more visibility than topology B**, not just a different account shape. As parent/broker, we can enumerate a sub-account's API keys, their permissions, and create/modify/delete them — none of which is possible for an account looking at its own key inventory. This flips several "NOT AVAILABLE" rows in the matrix below into "AVAILABLE (topology A only)." See §3.

You also confirmed customers include **active AI futures traders using up to 20x leverage**, not just accumulation/hold accounts. This breaks the brief's baseline assumption ("withdrawals are rare, trading is unusual") for a meaningful subset of accounts. See §6.

---

## 1. Signal Availability Matrix

Legend: 🟢 Available directly · 🟡 Available via proxy/derivation · 🔴 Not available (documented gap) · ⚠️ Verified to exist but schema/limit UNVERIFIED — needs a live-key spike in Phase 1 before we depend on it.

### Funds movement

| Detection | Status | Mechanism | Source |
|---|---|---|---|
| [CRITICAL] Withdrawal initiated, incl. in-flight/pending | 🟢 | `GET /api/v2/spot/wallet/withdrawal-records`. Status enum is a real state machine: `pending`, `pending_review`, `pending_fail`, `pending_review_fail`, `reject`, `success` — pollable *before* completion. CCXT: `fetchWithdrawals: True`. **All withdrawals, spot or futures-originated, funnel through this one spot endpoint** — Bitget has no separate futures withdrawal endpoint; funds must transfer to spot first. That's actually good news: one endpoint gives full withdrawal coverage regardless of which sub-ledger the attacker drains. | bitget.com/api-doc/spot/wallet/Get-Withdraw-Record; bitget.com/api-doc/spot/wallet/Wallet-Withdrawal |
| [CRITICAL] Withdrawal to never-seen address | ⚠️ | Withdrawal submission takes an `address` param, and the sibling deposit-records endpoint returns `fromAddress`/`toAddress`, so the withdrawal-records response almost certainly carries a destination-address field too — but the research pass did not get definitive confirmation of the exact field name on withdrawal-records. **Verify empirically with a live key in Phase 1** before wiring this rule. Fallback if the field is absent: cross-reference `clientOid`/`orderId` against Bitget's withdrawal confirmation email/UI export. | bitget.com/api-doc/spot/wallet/Get-Withdraw-Record (fields unconfirmed); bitget.com/api-doc/spot/wallet/Get-Deposit-Record (confirmed pattern) |
| [CRITICAL] Unexplained per-asset quantity decrease | 🟡 (derived) | Reconcile `all-account-balance` / per-product-line asset snapshots against withdrawal-records + fills + transfer-records + contract account bill. Built from confirmed-available primitives, not a single endpoint. | see rows below |
| [HIGH] Sub→main / sub→sub transfer pattern break | 🟢 (topology A); 🟡 (topology B) | `GET /api/v2/spot/account/sub-main-trans-record` returns `fromUserId`/`toUserId` directly — full counterparty visibility. Topology B customers without Bitget sub-accounts of their own don't have this signal; their internal spot↔futures transfers are visible via `transferRecords` but carry no counterparty ID (expected — same UID). | bitget.com/api-doc/spot/account/Get-SubAccount-TransferRecords; bitget.com/api-doc/spot/account/Wallet-Transfer |
| [HIGH] Burst of small withdrawals (structuring) | 🟢 (derived) | Same withdrawal-records feed as above, windowed. | — |

### Trading-based drain patterns

| Detection | Status | Mechanism | Source |
|---|---|---|---|
| [HIGH] Trades on illiquid pairs far from index/mark price | 🟢 futures; 🟡 spot | Futures: `history-mark-candles` + `history-index-candles` give a real mark/index series to diff fills against. Spot has no index-price concept at all (it's a derivatives construct) — the spot proxy is comparing fill price against the public ticker/order-book mid at trade time, which is weaker but the best available. | bitget.com/api-doc/contract/market/Get-History-Mark-Candle-Data; .../Get-History-Index-Candle-Data |
| [HIGH] Sudden activity in never-touched low-cap pairs | 🟢 (derived) | Baseline built from `spot/trade/fills` + `mix/order/orders-history`, both confirmed with full field sets (price, size, fee, timestamp, symbol). | bitget.com/api-doc/spot/trade/Get-Fills; .../contract/trade/Get-Orders-History |
| [MEDIUM] Abnormal fee-spend rate vs baseline | 🟢 | `GET /api/v2/common/trade-rate` gives the account's actual maker/taker rate (VIP-tier-adjusted) as the baseline; per-fill `feeDetail.totalFee` gives the actuals to compare against it. | bitget.com/api-doc/common/public/Get-Trade-Rate |

### Account & security state

| Detection | Status | Mechanism | Source |
|---|---|---|---|
| [CRITICAL] New API key created / permissions changed | 🔴 own/independent account · 🟢 sub-account (topology A, from parent) | No endpoint exists, anywhere, for an account to list its **own** API keys — confirmed absent by checking `Get Account Info` (which returns only the calling key's own scope, `authorities`, not sibling keys) and cross-checked against a third-party endpoint index. This is a hard gap for topology B and for a topology-A customer's *own* keys. For sub-accounts, the parent CAN list+diff: `GET /api/v2/user/virtual-subaccount-apikey-list`, `GET /api/v3/broker/query-sub-apikey`. **Proxy for the unlistable case**: we don't need to see the new key directly — any key an attacker creates still has to *act* through the account, and every action it takes (withdrawal, transfer, trade) is visible through the fund-movement and trading detections above. Treat those as the de facto proxy for "new key exists and is being used maliciously." | bitget.com/api-doc/spot/account/Get-Account-Info; bitget.com/api-doc/common/vsubaccount/Get-Virtual-Subaccount-List; github.com/tiagosiebler/bitget-api endpoint index (cross-check, confirmed no sibling-key-list endpoint) |
| [CRITICAL] Withdrawal whitelist disabled / new address added | ⚠️ | A **new** endpoint, `GET /api/v3/account/withdraw-address` ("query the withdrawal address book bound to the account"), appeared in Bitget's UTA changelog dated 2026-05-26 — genuinely new capability, but its full response schema (does it expose a whitelist on/off boolean, or just an address list?) could not be confirmed; the dedicated doc sub-page wasn't locatable. **Do not wire this into CRITICAL alerting until a live-key spike confirms the schema.** Until then, rely on the unseen-address proxy (row above) and document the gap honestly in alerts, as the brief requires. Do not conflate this with the `ips` field on Get-Account-Info, which is an *API-access* IP whitelist, not a *withdrawal address* whitelist — different feature. | bitget.com/api-doc/uta/changelog (2026-05-26 entry); bitget.com/api-doc/spot/account/Get-Account-Info |
| [HIGH] Our own read-only key deleted / permission errors | 🟢 | Directly observable by construction — our own polling calls will start failing with auth/permission errors the moment our key is deleted or downgraded. No proxy needed; this is a first-class signal for both topologies. Exact Bitget error codes for "key revoked" vs. "key permission insufficient" vs. transient network error were not enumerated in this pass — Phase 1 should catalog the actual error codes returned so the tamper signal isn't confused with an ops blip. | direct inference from polling behavior; error-code catalog is a Phase 1 TODO |
| [HIGH] New device/IP/geolocation login | 🔴 | Confirmed absent everywhere: no REST endpoint, no WS channel, no webhook. Bitget's own docs describe login history as a **UI-only** feature with a 3-month lookback, not exposed via API. This is a hard, permanent gap — document it in the runbook rather than pretending it's covered. Customer-side mitigations (Bitget anti-phishing code, device management, Bitget's own notification settings) belong in the runbook per the brief. | bitget.com/support/articles/12560603819255; bitget.com/academy/bitget-advanced-account-security-guide |
| 2FA enabled/disabled status | 🔴 | Same conclusion — UI-only, no API surface at all. Not in the original catalog but worth flagging since it's adjacent to the whitelist/login gaps and customers will ask. | bitget.com/academy/bitget-two-factor-authentication-2fa-guide |

### Operational heartbeat

| Detection | Status | Mechanism |
|---|---|---|
| [MEDIUM] Account unreachable / auth failures for N polls | 🟢 | Trivial — our own polling success/failure record. |
| [LOW] Baseline drift | 🟢 (derived) | Built from the same baseline store as everything else. |

---

## 2. CCXT-specific implementation notes (from live source, `master` branch, fetched 2026-07-02)

Source files confirmed to exist and fetched: `python/ccxt/bitget.py` (unified REST) and `python/ccxt/pro/bitget.py` (WebSocket). `master` is a moving branch — pin a commit SHA when Phase 1 actually starts coding, via `api.github.com/repos/ccxt/ccxt/commits?path=python/ccxt/bitget.py&sha=master`.

**Use these, not the naive unified call:**
- `fetchOrders` is `False` — use `fetchOpenOrders` / `fetchClosedOrders` / `fetchCanceledOrders` / `fetchCanceledAndClosedOrders` instead (all `True`).
- `fetchDeposit`/`fetchWithdrawal` (singular) are `False` — use the plural, filterable `fetchDeposits`/`fetchWithdrawals`.
- `fetchAccounts` is `False` — sub-account listing has to go through CCXT's *implicit* methods (`privateGet...`) calling the native broker/virtual-subaccount endpoints directly, not a unified method.
- `fetchTransfers`/`transfer`/`fetchLedger` are `True` and map cleanly to the endpoints confirmed above.

**WebSocket channels confirmed** (`python/ccxt/pro/bitget.py`): `watchBalance` → `account` channel, `watchOrders` → `orders` channel, `watchMyTrades` → `fill` channel, `watchPositions` → `positions` channel. All `True` in `has`. **None of these cover withdrawals, deposits, or transfers** — confirmed independently by the Bitget docs research (private WS channel list has no withdrawal/deposit/transfer topic). This means the fast loop for fund-movement detection is REST-only, unavoidably — WS only helps with the trading-drain detections, not the funds-movement ones, which are actually the CRITICAL-severity items. Design accordingly (§4).

**Endpoint map highlights relevant to topology**: the native `api` dict has a full `broker/*` section (`sub-create`, `sub-api-create`, `sub-list`, `sub-api-list`, `sub-withdrawal`, `sub-address`, etc.) and a parallel `user/*` virtual-subaccount section, both confirmed present. Notably `v2/mix/account/sub-account-assets` carries a CCXT weight of **200**, far above the weight-1/2/4 typical of most endpoints in this file — a strong signal (not a confirmed number) that this endpoint is either heavily throttled or intentionally expensive to call per-sub-account. This directly affects the topology-A rate-limit math in §4.

**Stack decision (§9 of brief):** no case for TypeScript. CCXT's Python and JS/TS builds are transpiled from a shared source pipeline, and the `pro` (WebSocket) implementation exists with full `watch*` support in the Python tree exactly as described — there's no material capability gap to justify switching. Staying on Python 3.11+ per the default.

---

## 3. Topology A vs. B — this is a real architecture fork, not a detail

This is the most consequential finding in this audit and changes the recommended default.

**Topology A (sub-account/broker) gives us more, not less, visibility:**
- We can enumerate a sub-account's actual API keys and permissions (`GET /api/v2/user/virtual-subaccount-apikey-list`, `GET /api/v3/broker/query-sub-apikey`) — turning the CRITICAL "new API key created" detection from a proxy into a direct, pollable signal. This is categorically unavailable for topology B.
- We can see `status` (`normal`/`freeze`/`deleted`) and `uTime` on sub-account keys, so key deletion/modification is diff-able.
- We (the broker) directly control sub-account API key creation/deletion, which also means **our own tamper-detection story is stronger**: if an attacker wanted to blind us, they'd need to compromise the sub-account AND get us (the parent) to revoke our own visibility, which is a much higher bar than just deleting a single read-only key on an independent account.

**Topology B (independent keys) is fundamentally capped:**
- No key-list, no key-creation detection, no whitelist visibility beyond the unverified new endpoint, no login history. The four hardest gaps in the matrix above (new-key, whitelist, login, 2FA) are *all* topology-B-only gaps in practice.
- Its ceiling is customer trust in a single read-only key; our detection is entirely behavioral (fund movement + trading pattern), never key/session-state-based.

**Recommendation:** where a customer is willing to onboard as a sub-account under our broker program, do it — it's a strictly better security posture for both them and us, not just an operational convenience. For customers who insist on keeping their own primary account (topology B), be explicit in the customer-facing runbook that new-API-key and login-session monitoring is *not* possible on Bitget's current API, and lean on Bitget's own account-side protections (anti-phishing code, IP whitelist restriction on the granted key, 2FA) as compensating controls we instruct the customer to enable themselves.

Given the mix confirmed for launch (both topologies), the ingestion pipeline needs an explicit `topology` field per account from day one, since it changes which endpoints get called (broker/* vs. independent private/*) and which detections are even wired on for that account — not just a cosmetic tag.

---

## 4. Rate-limit capacity math

All numbers below are **confirmed with citations** except where marked UNVERIFIED.

**Confirmed limits:**
- Global egress-IP ceiling: **6,000 requests/minute/IP** (100 req/sec sustained), with a 5-minute penalty box on breach. (bitget.com/wiki/bitget-api-rate-limits)
- Per-UID (per account), confirmed at **10 req/sec** each: withdrawal-records, deposit-records, transfer-records/sub-main-trans-record, spot fills (standard tier — copy-trading tier drops to 1/sec), contract order-history, contract account bill, trade-rate.
- `all-account-balance` (best single balance-snapshot endpoint): **1 req/sec/UID** — noticeably tighter than everything else.
- Broker/sub-account endpoint limits (`sub-future-assets`, `sub-api-list`, etc.): **UNVERIFIED** — not published on the pages fetched. CCXT's weight-200 tag on the equivalent mix endpoint is the only hint we have, and it points toward this being the tightest budget in the system, not the most generous.
- WebSocket connection/subscription limits (max connections per IP/UID, per-connection subscription cap): **NOT FOUND** in Bitget's docs at all. Design conservatively and confirm empirically in Phase 1/2 rather than assuming a number.

### Topology B (independent keys) — per-account UID budget is not the bottleneck

Mandatory REST fast-loop calls (no WS coverage exists for these): withdrawal-records, deposit-records, transfer-records = **3 calls/account/cycle**.

At a 60s cadence per account: 3 calls / 60s = 0.05 req/sec — **0.5% of that single account's own 10 req/sec budget**. The per-UID limit is never the constraint here; each customer has their own independent bucket.

The real constraint is **our own egress IP budget**, shared across every account we poll from it:

| Cadence | Calls/sec at 1,000 accounts | % of one IP's 100 req/sec budget |
|---|---|---|
| 60s fast loop (3 endpoints) | 50 req/sec | 50% |
| 30s fast loop (3 endpoints) | 100 req/sec | 100% — zero headroom, not viable on one IP |
| Slow loop (2 endpoints / 10 min, incl. the 1 req/sec-capped balance call) | +3.3 req/sec | +3.3% |
| WS-dead reconciliation poll of fills/orders/balance every 5 min (3 endpoints) | +10 req/sec | +10% |
| **Total, 60s cadence + reconciliation** | **~63 req/sec** | **~63%** |

**Recommendation:** don't run this off one egress IP. Spread the worker fleet across at least 3–4 distinct outbound IPs to hold steady-state utilization under ~40% per IP, which leaves burst/retry headroom and room to grow to roughly 3,000–4,000 accounts before adding IPs. Push everything that has WS coverage (orders, fills, positions, balance) onto WebSocket streams and treat REST as backstop/reconciliation only — this is not optional at 1,000+ accounts, it's what makes the 30–60s cadence the brief asks for achievable at all, since REST alone at 30s cadence already saturates a single IP with zero slack.

### Topology A (sub-account/broker) — the actual open question

This is the finding that most needs your attention before Phase 1 commits to a design: **we don't know if sub-account fund-movement data is queryable per-sub-account or in a single batched call across all sub-accounts**, and the difference determines whether topology A can hit a 60s cadence at all.

- If broker/virtual-subaccount endpoints are rate-limited **per parent UID** (as their naming and the CCXT weight-200 hint suggest) and require **one call per sub-account**, then 1,000 sub-accounts against a ~10 req/sec parent budget means one full pass takes **≥100 seconds minimum**, before adding any other endpoint calls — a 60s target cadence is arithmetically impossible.
- If instead these are **list endpoints that return all sub-accounts in one paginated call** (plausible given the "list" naming, e.g. `sub-future-assets`, `sub-main-trans-record`), the cost is O(pages), not O(accounts), and this whole constraint disappears.

**This needs an empirical spike before Phase 1 scales topology A**: stand up a real broker test account with 2–3 sub-accounts and observe actual response shape and enforced RPS on `broker/account/sub-future-assets`, `sub-main-trans-record`, and `virtual-subaccount-apikey-list`. Until that's done, treat topology A's fast-loop cadence as unverified and don't promise it to sub-account customers ahead of the trading-pattern/withdrawal detections, which are proven achievable regardless of topology.

### WebSocket authentication note affecting both topologies

Bitget's private WS auth is a signed login message per connection (`apiKey` + `passphrase` + HMAC-SHA256 signed timestamp, 30s expiry) — not a shared listen-key model. Practically, **each customer's API key needs its own WS connection**; connections are not poolable across customers. Whether a broker's parent credentials can open one WS connection and receive multiple sub-accounts' channels, or whether each sub-account still needs its own connection with its own key, was **not confirmed** in this pass and should be checked in the same broker-account spike above — it directly affects how many concurrent WS connections the topology-A architecture needs at 1,000+ accounts.

---

## 5. Architecture recommendation (subject to your approval)

- **Language/runtime:** Python 3.11+, `ccxt` — no justification found to switch to TS (§2).
- **Fast loop (money movement, mandatory REST):** withdrawal-records, deposit-records, transfer-records per account, 60s cadence + jitter, spread across ≥3 egress IPs. This is the one loop that can never move to WebSocket, because Bitget doesn't expose those events over WS at all.
- **Fast loop (trading/balance, WS-first):** `watchOrders`, `watchMyTrades`, `watchBalance`, `watchPositions` per account via CCXT `pro`, with a REST reconciliation poll every 5 min per account as a dead-stream safety net (silence ≠ no events, per the brief).
- **Slow loop (config/security snapshot):** account-info (`ips` field), all-account-balance, and — topology A only — sub-account API key list, every 5–15 min.
- **Queue/workers:** Redis-backed queue, horizontally scalable workers, per-account idempotent tasks with jitter, matches §7 of the brief. Token-bucket rate limiter keyed by *both* credential (UID) and egress IP, seeded from the confirmed numbers in §4, not estimates.
- **Circuit breakers:** per-account, triggered by the auth/permission-error tamper signal (§1) so one revoked or banned key/IP never stalls the fleet.
- **Storage:** Postgres + Redis, Docker Compose, per brief. Append-only event log, Decimal everywhere, UTC timestamps — no changes needed to that part of the brief; it holds up against what's actually available.
- **Alert channels:** all four requested channels (Telegram, SMS/Twilio, phone, email) are available to wire in Phase 1; no blockers found here, this is pure integration work, not research.
- **Baseline profiles must be per-account-type, not global** — see §6, this is a change from the brief's implicit single-baseline assumption.

---

## 6. Flag: AI futures 20x-leverage accounts break the brief's baseline assumption

The brief frames withdrawals as rare and trading itself as anomalous ("accumulation/hold accounts"). You've confirmed the customer base also includes **active AI-driven futures traders at up to 20x leverage**. Two concrete conflicts this creates, to resolve in Phase 1 design (not blocking Phase 0 approval, but flagging now so it's not a surprise later):

1. **Trading-anomaly baselines (§4 catalog items: illiquid-pair drain, never-touched-pair activity, fee-spend rate)** need a per-account-type profile. A hold account trading a new low-cap alt for the first time is a strong signal; an AI futures bot doing the same as part of a strategy rotation is normal. Onboarding needs an explicit account-type tag (hold / discretionary-trader / AI-leveraged-futures) that selects detection thresholds, not one global baseline.
2. **"Unexplained asset quantity decrease" (CRITICAL) will false-positive on liquidations** at 20x leverage unless liquidation events are explicitly reconciled as an explaining event, alongside trades/transfers/withdrawals. Whether Bitget's contract account-bill `businessType` enum includes a distinct liquidation value was **not confirmed** in this research pass (only a few `businessType` examples were seen: `trans_from_exchange`, `trans_to_contract`, `trans_from_cross`) — this needs to be checked directly against a live contract account-bill response in Phase 1 before the quantity-decrease rule goes live for any leveraged-futures account, or it will alert on every liquidation.

---

## 7. Explicitly UNVERIFIED — do not build against these without a Phase 1 live-key spike

1. Exact field name for destination address on `withdrawal-records` response (§1, row 2).
2. Full response schema of the new `GET /api/v3/account/withdraw-address` endpoint — does it expose a whitelist toggle or just an address list? (§1, row: whitelist)
3. Actual enforced RPS on broker/virtual-subaccount endpoints (`sub-future-assets`, `sub-api-list`, `sub-main-trans-record`) — parent-UID-shared budget, unknown number (§4, topology A math).
4. Whether broker sub-account list/asset endpoints return all sub-accounts per call (batched) or require one call per sub-account (§4, topology A math — this is the load-bearing unknown for topology A's cadence).
5. WebSocket connection/subscription limits (max connections per IP/UID) — not published anywhere found (§4).
6. Whether a parent broker WS connection can receive multiple sub-accounts' private channels, or each sub-account needs its own WS connection (§4, WS auth note).
7. Whether Bitget's contract account-bill `businessType` enum has a distinct liquidation code, needed to avoid false-positiving the quantity-decrease rule on 20x-leverage liquidations (§6).
8. Bitget's actual error codes for "API key revoked" vs. "insufficient permission" vs. transient failure, needed to make the tamper-signal detection (§1, row: our key deleted) reliable rather than noisy (§1).
9. Exact rate-limit tier table for higher-volume/VIP accounts beyond the base 10 req/sec figures — a "300 req/sec at top tiers" figure was mentioned on Bitget's rate-limit wiki overview but the full tier breakdown wasn't located.

Recommend resolving #3, #4, and #6 (all topology-A sub-account cadence questions) before committing engineering time to topology A's polling architecture specifically — they determine whether the sub-account fast loop is even achievable at 1,000+ scale, which is the whole premise of offering topology A. #1, #2, #7, #8 are cheap to verify with any single live key/account and should just be the first thing Phase 1 does.

---

## Next step

This report is the Phase 0 deliverable per your spec. Per your working style instructions, I'm stopping here for your review rather than proceeding into Phase 1. Two things worth a quick decision before I start Phase 1:

1. Are you fine starting Phase 1's 3 pilot accounts as topology B (independent keys) since that path has no open unknowns, while the topology-A sub-account spike (items #3/#4/#6 above) runs as a parallel, non-blocking investigation?
2. Do you have (or can you get) a Bitget broker-tier test account with 2–3 sub-accounts, so the topology-A unknowns can actually be resolved empirically rather than staying flagged indefinitely?

---

## Addendum — live-key findings (2026-07-02, Phase 1)

Two things confirmed against a real account during Phase 1 build-out, resolving items that were previously inferred from docs rather than observed:

1. **The `authorities` read/write suffix convention is real, not just documented.** A live full-access key returned `authorities: ['wtow', 'pllw', 'chow', 'smow', 'coow', 'taxw', 'p2pw', 'cpow', 'ttow', 'stow']` — every token ends in `w`, and `coow` appears verbatim, matching the `coor`/`coow` pattern item #8-adjacent flagged as needing verification. The onboarding gate (`sentinel/onboarding.py`) correctly rejected this key on first live use. This also confirms the `ips` field is genuinely `None` when no IP whitelist is set on a key — worth recommending to customers as a defense-in-depth step once Sentinel's egress IP(s) are known.

2. **New finding, not previously flagged: `ccxt.bitget.fetch_my_trades()` requires a `symbol` argument for classic (non-UTA) accounts** — confirmed by reading the installed CCXT source (`ArgumentsRequired` raised when `symbol is None` and `uta` isn't set). This breaks the original assumption that `fetchMyTrades: True` meant an all-symbols pull was available the same way `fetchWithdrawals`/`fetchDeposits` support `code=None`. Fixed by switching the quantity-drop reconciliation's fill data source to `fetch_closed_orders()` (confirmed to accept `symbol=None`), using the order's executed `filled` quantity rather than requested `amount`. Net effect: no architecture change needed, but this would have been a hard runtime failure on every fast-loop cycle if it had shipped without a live-key test catching it first — a concrete argument for running Phase 1 against at least one real account before onboarding pilot customers.
