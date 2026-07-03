# Sentinel — Customer Incident Runbook

You received an alert from Sentinel. This page tells you what it means and
what to do, in order. Minutes matter for CRITICAL alerts.

## If you receive a CRITICAL alert

**Do these immediately, in this order:**

1. **Freeze the account** in the Bitget app: Profile → Security → Freeze
   Account. This blocks withdrawals, trades, and API access at once and is
   reversible.
2. **Revoke all API keys**: Profile → API Management → delete every key you
   don't positively recognize — and if in doubt, delete all of them
   (including ours; monitoring can be re-onboarded later, your funds can't).
3. **Check your withdrawal whitelist**: Assets → Withdraw → Address
   Management. Remove any address you didn't add, and turn whitelist-only
   withdrawals ON.
4. **Verify 2FA is still yours**: Security → Google Authenticator / SMS. If
   2FA was changed or removed, treat the account as fully compromised.
5. **Contact Bitget support** (in-app chat) and report unauthorized access.
   Ask them to flag in-flight withdrawals — a `pending`/`pending_review`
   withdrawal can sometimes still be stopped; a `success` one cannot.
6. Reply to or acknowledge the Sentinel alert so escalation stops
   (`sentinel ack <id>` on our side — just tell us you've seen it).

## What each alert type means

| Alert | Meaning | Most likely benign cause |
|---|---|---|
| `withdrawal_initiated` | A withdrawal exists on your account, possibly still in-flight | You made it yourself |
| `withdrawal_to_unseen_address` | Withdrawal to an address never seen in your history | Your own brand-new wallet |
| `withdrawal_burst` | Several withdrawals inside a short window | Manual consolidation you performed |
| `unexplained_quantity_decrease` | Coins left your account with no matching trade/withdrawal record | Exchange-side dust/fee events; futures liquidation (leveraged accounts) |
| `transfer_route_never_seen` | Internal transfer along a route you've never used | First time using futures/earn |
| `never_touched_pair_activity` | Trading in a pair you've never traded | You (or your bot) started a new strategy |
| `off_market_execution` | A fill far from market price on an illiquid pair | Extreme volatility; thin book slippage |
| `abnormal_fee_spend` | Fee spend far above your baseline | High-volume day for your bot |
| `account_permission_state_changed` | Your account's API permission/IP state changed | You edited API settings yourself |
| `monitoring_key_tamper_suspected` | OUR key stopped working — attackers often delete monitoring first | You rotated/deleted our key yourself |
| `account_unreachable` | We can't reach your account | Bitget maintenance/outage |

If the "benign cause" column matches something you actually did in the last
hour, tell us and we'll acknowledge the alert. **If it doesn't, treat it as
real and run the CRITICAL steps above.**

## What Sentinel cannot see (be honest with yourself about these)

Bitget's API exposes no way for us to monitor these — you are the only line
of defense on them:

- **Logins from new devices/locations.** Enable Bitget's own login
  notifications: Security → Notification Settings.
- **2FA being disabled.** Nothing external can watch this; use an
  authenticator app (not SMS) and never share codes.
- **New API keys being created** (for independent accounts). We detect the
  *consequences* (withdrawals, trades) fast, but not the key creation
  itself.

## Prevention checklist (do these once, today)

- Set an **anti-phishing code** (Security → Anti-Phishing Code) so real
  Bitget emails are distinguishable from phishing.
- Turn **withdrawal whitelist ON** and add only your own addresses.
- Use an **authenticator app** for 2FA, not SMS.
- Review **API Management** monthly; delete keys you don't use.
- The API key you give Sentinel must be **read-only** — we verify this
  programmatically and will reject anything broader. Never give any service
  (including us) trade or withdraw scopes for monitoring.
