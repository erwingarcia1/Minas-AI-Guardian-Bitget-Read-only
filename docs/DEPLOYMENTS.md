# Deployment log

What is actually running in production, with the evidence it was checked.

This file exists because **"production runs the July build" was carried
through internal documents for six weeks without anyone logging in to
check — and it was wrong.** Production was 1.1.0, two commits past the
tag, built from a non-release commit. The version in prose drifted from
the version on disk because nothing required a command output next to the
claim. So the rule here: **every entry carries evidence** — measured, not
recalled. Future deploys append an entry.

The measurement commands, for every entry:

```bash
sudo docker compose exec -T sentinel sentinel version   # what the container reports
sudo git -C /opt/minas-guardian describe --tags         # what is on disk
```

Newest entry first.

---

## 2026-10-26 · v1.18.0 (current, from v1.17.0): a refused security tripwire pages Minas; the reset link by email only

Deployed by the owner over SSH with the EC2 key pair at `57.181.51.14`,
the command typed with the `!` prefix and its output read by the session
(docs/deployment.md "Deploying code changes"). Cut the same day from
`main` at `c49e3e0` (the merge of #140, 12:30) by `scripts/release.sh
1.18.0`, run in the main checkout on `main`: the CI workflow was
dispatched on `c49e3e0` (run 40323982781, its scheduled-gates job 12:37
to 12:52, probes and mutation floor green), and the scheduled-gates cover
accepted that run as covering the tree, so the fast gates ran locally -
ruff, the suite (368 s), the coverage floor, pip-audit, the issue-status
audit, "scheduled gates ran (8d)" and release objects, with the
design-guard probes and the mutation floor skipped under `--fast` as
already run; release commit `7fc8e7c`, annotated tag `v1.18.0`, the
GitHub Release published (13:00:07), CI on the release commit green (run
40326886468, 13:00 to 13:07). Times are UTC.

**The deploy:** `sudo /opt/minas-guardian/scripts/deploy.sh v1.18.0`,
finished by 15:04 (the command's output reached the session at
15:04:28) - preflight clean; tags fetched; running before: 1.17.0 at
`22d7fec`; checkout `7fc8e7c`, detached; `deploy-gate: OK - HEAD is
v1.18.0 (7fc8e7c)`; the image built from a 417 kB context, `pip`
installing `sentinel-1.18.0` (with `ccxt-4.5.63`, `cryptography-50.0.2`,
`anthropic-2.3.0` and `segno-1.6.6` among its dependencies, as in
1.17.0); the live `config/rules.yaml` validated against the new image
("valid — every setting is recognised and in range"); the switchover
recreated `minas-guardian-sentinel-1` only and left `caddy` running;
healthy; the version assertion:

```
Minas AI Guardian 1.18.0
  commit   7fc8e7ce855d30d2412104be188cc879313d33e5
  tag      v1.18.0
  source   build-arg
deploy: OK — v1.18.0 is live and verified
```

**The readings afterwards** (from outside, by the session, 15:06; the
on-box `git describe` and `compose ps` readings were not taken - the
version assertion above is the container's own answer):

```
curl -o /dev/null -w '%{http_code} %{redirect_url}' /healthz    -> 303 https://guardian.minastrade.com/
curl ... /alerts (no session)                                   -> 303 https://guardian.minastrade.com/
curl / | grep -c 'id="landing-title"'                           -> 1   (the front door is unchanged)
curl / | grep -c 'signin-title'                                 -> 0
curl / | grep -c 'Our address has changed'                      -> 0   (the host is recognised)
curl /login | grep -c 'name="password"'                         -> 1
curl /forgot                                                    -> 200, the form (action="/forgot")
curl /reset (no link, no session)                               -> 200, the sign-in page (id="signin-title")
curl /reset?token=<not a token>                                 -> 200, the reset form - nothing is checked or spent on GET
curl -b 'minas_portal=stale' /forgot                            -> 200, the form
curl /forgot (headers)                                          -> cache-control: no-store; strict-transport-security: max-age=31536000;
                                                                   x-frame-options: DENY; content-security-policy: frame-ancestors 'none'
curl /api/v1/limits                                             -> 401 (the app API)
curl /admin                                                     -> 401 (the dashboard's sign-in, still only under /admin)
curl http://guardian.minastrade.com/                            -> 308 https://guardian.minastrade.com/
```

Not read from outside, because each needs a member, a refusing provider
or an operator session: the `security_notice_undelivered` page itself,
and the reset email's single channel. The tests and the design-guard
probes are their evidence (CHANGELOG `[1.18.0]`).

**What shipped** (CHANGELOG `[1.18.0]`): a refused "password changed",
"two-factor switched off" or "alerts going somewhere new" notice pages
Minas at CRITICAL - masked address, the account or the login's accounts,
the server's words masked, `failed_at` pointing at the failed row - and a
connected Telegram chat that took the copy is counted, never a reason to
skip the page (decision 26b); `sentinel set-contact` builds an operator
alerter for that notice; the reset link goes to the mailbox only (26c);
the v1.17.0 deploy record.

**Still outside this entry:** decisions 26d, 26e and 26b's two further
tripwires (the owner's); the pending keys and the SMS provider
(CHANGELOG's pending list); the Tailscale admin-console removal of the
machine.

---

## 2026-10-25 · v1.17.0 (current, from v1.16.0): the reset path, failed email on the overview, no false "delivered to nobody" pages, portal access follows a contact move

Deployed by the owner over SSH with the EC2 key pair at `57.181.51.14`,
the command typed with the `!` prefix and its output read by the session
(docs/deployment.md "Deploying code changes"). Cut the same afternoon
from `main` at `902270b` (the merge of #138, 13:43) by
`scripts/release.sh 1.17.0`, run in the main checkout on `main`: the CI
workflow was dispatched on `902270b` (run 40201378747, its
scheduled-gates job 14:15 to 14:32, probes and mutation floor green),
and the scheduled-gates cover accepted that run as covering the tree, so
the fast gates ran locally - ruff, the suite (735 s), the coverage floor,
pip-audit, the issue-status audit, "scheduled gates ran (8d)" and release
objects, with the design-guard probes and the mutation floor skipped
under `--fast` as already run; release commit `22d7fec` (14:46:54),
annotated tag `v1.17.0`, the GitHub Release published (14:47:05), CI on
the release commit green (run 40203850604, 14:47 to 14:54). Times are
UTC.

**The deploy:** `sudo /opt/minas-guardian/scripts/deploy.sh v1.17.0`,
between 14:54 and 14:57 - preflight clean; tags fetched; running before:
1.16.0 at `fb70525`; checkout `22d7fec`, detached; `deploy-gate: OK -
HEAD is v1.17.0 (22d7fec)`; the image built from a 527 kB context, `pip`
installing `sentinel-1.17.0` (with `ccxt-4.5.63`, `cryptography-50.0.2`,
`anthropic-2.3.0` and `segno-1.6.6` among its dependencies, as in
1.16.0); the live `config/rules.yaml` validated against the new image
("valid — every setting is recognised and in range"); the switchover
recreated `minas-guardian-sentinel-1` only and left `caddy` running;
healthy; the version assertion:

```
Minas AI Guardian 1.17.0
  commit   22d7fecbc58b8d93758207f5864d52065d9a8619
  tag      v1.17.0
  source   build-arg
deploy: OK — v1.17.0 is live and verified
```

**The readings afterwards** (from outside, by the session, 14:57; the
on-box `git describe` and `compose ps` readings were not taken - the
version assertion above is the container's own answer):

```
curl -o /dev/null -w '%{http_code} %{redirect_url}' /healthz    -> 303 https://guardian.minastrade.com/
curl ... /alerts (no session)                                   -> 303 https://guardian.minastrade.com/
curl / | grep -c 'id="landing-title"'                           -> 1   (the front door is unchanged)
curl / | grep -c 'signin-title'                                 -> 0
curl / | grep -c 'Our address has changed'                      -> 0   (the host is recognised)
curl /login | grep -c 'name="password"'                         -> 1
curl /forgot                                                    -> 200, the form (action="/forgot")
curl /reset (no link, no session)                               -> 200, the sign-in page (id="signin-title"), as before
curl /reset?token=<not a token>                                 -> 200, the reset form - nothing is checked or spent on GET
curl -b 'minas_portal=stale' /forgot                            -> 200, the form (the recovery routes answer before the session check)
curl /forgot (headers)                                          -> cache-control: no-store; strict-transport-security: max-age=31536000;
                                                                   x-frame-options: DENY; content-security-policy: frame-ancestors 'none'
curl /api/v1/limits                                             -> 401 (the app API)
curl /admin                                                     -> 401 (the dashboard's sign-in, still only under /admin)
curl http://guardian.minastrade.com/                            -> 308 https://guardian.minastrade.com/
```

Not read from outside, because each needs a session or a real member: a
signed-in browser at `/forgot` and on the emailed link, the reset
email's timing for an address with a login, the "Failed email
deliveries" card (it renders only when a failed email row exists), the
liveness window and the operator pages. The tests and the design-guard
probes are their evidence (CHANGELOG `[1.17.0]`).

**What shipped** (CHANGELOG `[1.17.0]`): the reset request on a limiter
of its own; the reset email after the answer; the recovery pages for a
browser signed in to the portal; the liveness Email window counting
sends only; the "Failed email deliveries" card on the ops overview; no
CRITICAL "delivered to nobody" page for an alert left to the digest or
snoozed by a member (never for a coverage alert), and the page naming
the tier; `set-contact` revoking the old address's scoped portal access
with a move, printing the new address's login state, refusing a
phone-only change, and a notice that says only what is true (decision
26a); the v1.16.0 deploy record.

**Still outside this entry:** the portal login for the address in the
2026-10-25 report, issued from the dashboard (the owner's); decision
26b, 26c and 26d; the pending keys and the SMS provider (CHANGELOG's
pending list); the Tailscale admin-console removal of the machine.

---

## 2026-10-24 · v1.16.0 (current, from v1.15.0): the landing page, on the web and in the app

Deployed by the owner over SSH with the EC2 key pair at `57.181.51.14`,
the command typed with the `!` prefix and its output read by the session
(docs/deployment.md "Deploying code changes"). Cut the same evening from
`main` at `474ab55` (the merge of #135, 23:07) by `scripts/release.sh
1.16.0`, run in the session's worktree on a `release-1.16.0` branch
published first so the script's bare `git push` had a same-named
upstream, then fast-forwarded onto `main` and the branch deleted: the
scheduled-gates cover accepted the dispatched run on `474ab55` itself
(run 40142236669, 23:19 to 23:35, probes and mutation floor green) as
covering the tree, so the fast gates ran locally - ruff, the suite
(440 s), the coverage floor, pip-audit, the issue-status audit,
"scheduled gates ran (8d)" and release objects, with the design-guard
probes and the mutation floor skipped under `--fast` as already run;
release commit `fb70525` (23:43:57), annotated tag `v1.16.0`, the GitHub
Release published (23:44:09), CI on the release commit green (run
40144002099, 23:45 to 23:52). Times are UTC.

**The deploy:** `sudo /opt/minas-guardian/scripts/deploy.sh v1.16.0`,
between 23:52 and 23:56 - preflight clean; tags fetched; running before:
1.15.0 at `4a5941d`; checkout `fb70525`, detached; `deploy-gate: OK -
HEAD is v1.16.0 (fb70525)`; the image built from a 248 kB context, `pip`
installing `sentinel-1.16.0` (with `segno-1.6.6`, `ccxt-4.5.63`,
`cryptography-50.0.2` and `anthropic-2.3.0` among its dependencies); the
live `config/rules.yaml` validated against the new image ("valid — every
setting is recognised and in range"); the switchover recreated
`minas-guardian-sentinel-1` only and left `caddy` running; healthy; the
version assertion:

```
Minas AI Guardian 1.16.0
  commit   fb70525e380756d74605a16d8b1e0dcd33268166
  tag      v1.16.0
  source   build-arg
deploy: OK — v1.16.0 is live and verified
```

**The readings afterwards** (from outside, by the session, 23:56; the
on-box `git describe` and `compose ps` readings were not taken this
time - the version assertion above is the container's own answer):

```
curl -o /dev/null -w '%{http_code} %{redirect_url}' /healthz    -> 303 https://guardian.minastrade.com/   (the runbook's new smoke check)
curl ... /alerts (no session)                                   -> 303 https://guardian.minastrade.com/
curl / | grep -c 'id="landing-title"'                           -> 1   (the landing page is the front door)
curl / | grep -c 'Our only address'                             -> 1   (the one address, from the setting)
curl / | grep -c 'Our address has changed'                      -> 0   (the host is recognised)
curl / | grep -c 'signin-title'                                 -> 0   (the form is no longer at /)
curl /  (step 2's numbers)                                      -> "about every 45 seconds; permissions and allowlists about every 10 minutes" (the box's rules.yaml)
curl /login | grep -c 'name="password"'                         -> 1   (the sign-in form, at /login)
curl /login | grep -c 'Our only address'                        -> 1
curl -b 'minas_portal=stale' / (headers)                        -> 200; set-cookie: minas_portal=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax;
                                                                   strict-transport-security: max-age=31536000; x-frame-options: DENY;
                                                                   content-security-policy: frame-ancestors 'none'
curl -b 'minas_portal=stale' / | grep -c 'You were signed out'  -> 1   (a rejected cookie says so, once, and is cleared)
curl /api/v1/limits                                             -> 401 (the app API)
curl /admin                                                     -> 401 (the dashboard's sign-in, still only under /admin)
curl http://guardian.minastrade.com/                            -> 308 https://guardian.minastrade.com/
```

**What shipped** (CHANGELOG `[1.16.0]`): the landing page as the portal's
front door, the sign-in at `/login`, an ended session landing on the
front door with "You were signed out." (decision 25); the `ui-parity`
skill and its hook; the v1.15.0 deploy record. The app's welcome screen
with the same sentences is in the app bundle, which reaches a phone
through Expo Go or a store build, not through this deploy.

**Still outside this entry:** the Tailscale admin-console removal of the
machine; the pending keys and the SMS provider (CHANGELOG's pending
list); #125's wording; the owner's own look at `/` and `/login` in a
browser and at the welcome screen on a phone; the Apple Developer
Program, without which there is no iPhone build beyond Expo Go.

---

## 2026-10-24 · v1.15.0 (current, from v1.14.0): the QR enrolment and the dashboard in the Minas identity

Deployed by the owner over SSH with the EC2 key pair at `57.181.51.14`,
the command typed with the `!` prefix and its output read by the session
(docs/deployment.md "Deploying code changes"). Cut the same day from
`main` at `cede369` by `scripts/release.sh 1.15.0`, 16:38 to 16:49: the
scheduled-gates cover accepted the dispatched run on `f82ce64` (the
dashboard commit; run 40108952008) as covering the tree, so the fast
gates ran locally - ruff, the suite (579 s), the coverage floor,
pip-audit, the issue-status audit, "scheduled gates ran (8d)" and
release objects, with the design-guard probes and the mutation floor
skipped under `--fast` as already run; release commit `4a5941d`
(16:49:34), annotated tag `v1.15.0`, the GitHub Release published, CI on
the release commit green (run 40113343239, 16:56). Times are UTC.

**The deploy:** `sudo /opt/minas-guardian/scripts/deploy.sh v1.15.0` -
preflight clean; tags fetched; running before: 1.14.0 at `2b7b6c8`;
checkout `4a5941d`, detached; `deploy-gate: OK - HEAD is v1.15.0`; the
image built from a 670 kB context, `pip` installing `sentinel-1.15.0`
with `segno-1.6.6` among its dependencies; the live `config/rules.yaml`
validated against the new image ("valid — every setting is recognised
and in range"); the switchover recreated `minas-guardian-sentinel-1`
only and left `caddy` running (the v1.14.0 oddity was a later `up -d`,
which this deploy did not run); the new container up at 16:58:54 by its
own log; healthy; the version assertion:

```
Minas AI Guardian 1.15.0
  commit   4a5941d0207fb5c02cb14ba461c58f32ce8b0f15
  tag      v1.15.0
  source   build-arg
deploy: OK — v1.15.0 is live and verified
```

**The readings afterwards** (the owner's second `!` line, read here;
about 17:03 - `sentinel` "Up 4 minutes"):

```
sudo git describe --tags                        -> v1.15.0 (no drift suffix)
sudo docker compose ps                          -> caddy Up 8 hours (0.0.0.0 and [::] on 80 and 443) - left running by the deploy;
                                                   sentinel Up 4 minutes (healthy), 127.0.0.1:8390-8391 and 127.0.0.1:9109 only
sudo docker compose exec -T sentinel sentinel version
  Minas AI Guardian 1.15.0 · commit 4a5941d0207fb5c02cb14ba461c58f32ce8b0f15 · tag v1.15.0 · source build-arg
sentinel log, 16:58:54                          -> "dashboard: X-Forwarded-For is believed from 10.231.0.2 only"
                                                   "portal: X-Forwarded-For is believed from 10.231.0.2 only"; no "no trusted proxy" line
curl /admin/login | grep -c '<h1>Ops dashboard</h1>'   -> 1   (the runbook's smoke check; the ops view's marking)
curl /login       | grep -c '<h1>Ops dashboard</h1>'   -> 0   (the portal untouched)
curl /admin/login | grep -c 'Operations'               -> 2   (the lockup, and the CSS comment in the served style block that names it)
curl /login       | grep -c 'Our only address'         -> 1
```

**What shipped** (CHANGELOG `[1.15.0]`): the QR code on the dashboard's
and the portal's enrolment pages and in `operator add` (decision 23,
`segno`); the dashboard in the Minas identity on the portal's dark ground
with the operations marking (decision 24); and the day's records - the
members' email, the second operator, the repository cleanup.

**Still outside this entry:** the Tailscale admin-console removal of the
machine; the pending keys and the SMS provider (CHANGELOG's pending
list); #125's wording; the owner's own look at `/admin` in a browser.

---

## 2026-10-24 · v1.14.0 (current, from v1.12.0): the public address live, the portal and the dashboard at /admin behind Caddy, alerting restored after the 2026-09-28 pause

Deployed by the owner over SSH with the EC2 key pair at `57.181.51.14`,
each command typed with the `!` prefix and its output read by the session
before the next (docs/deployment.md "The public address", steps 6 to 9,
then "Restoring after the 2026-09-28 pause"). Steps 1 to 5 - the SSH
proof, DNS, the security group, the Tailscale teardown and the clean box -
are the entry below. Times are UTC.

**Step 6, the deploy:** `sudo /opt/minas-guardian/scripts/deploy.sh
v1.14.0` pulled `caddy:2.11.7@sha256:504a44d7…`, created the `caddy-data`
and `caddy-config` volumes, recreated `minas-guardian-sentinel-1` on the
new image and started both containers; the healthcheck passed and the
version assertion printed:

```
Minas AI Guardian 1.14.0
  commit   2b7b6c814afe05c4bcc44636f381ae63fb23becf
  tag      v1.14.0
  source   build-arg
deploy: OK — v1.14.0 is live and verified
```

**Step 7, what runs:** `docker compose ps` → `caddy` Up, `sentinel` Up
(healthy). `ss -tlnp` → 80 and 443 on `0.0.0.0` and `[::]`
(`docker-proxy`); 8390, 8391 and 9109 on `127.0.0.1` only; nothing on a
`100.` address. The sentinel log at 09:02:19: `dashboard: X-Forwarded-For
is believed from 10.231.0.2 only`, `dashboard: served under /admin -
every link, form and redirect carries it; the bare /healthz still answers
for the container probe`, `portal: X-Forwarded-For is believed from
10.231.0.2 only`. One oddity, recorded: the first `docker compose up -d`
after deploy.sh recreated the `caddy` container once (sentinel stayed
`Running`); a second `up -d` left both `Running`, and the certificate the
first container had obtained persisted in `caddy-data` (the new
container's log shows `serving initial configuration` and no error, and
the certificate below is dated 08:03, before the recreate). Not
understood; watch whether a later `up -d` does it again.

**Step 8, the answers from outside** (from the owner's machine):

```
subject=CN=guardian.minastrade.com
issuer=C=US, O=Let's Encrypt, CN=YE1
notBefore=Oct 24 08:03:43 2026 GMT   notAfter=Jan 22 08:03:42 2027 GMT
GET /login            HTTP/2 200; content-security-policy: frame-ancestors 'none';
                      strict-transport-security: max-age=31536000; x-frame-options: DENY
GET /api/v1/limits    401
GET http://…/login    308 -> https://guardian.minastrade.com/login
GET /admin/login      HTTP/2 200; the three above plus cache-control: no-store,
                      x-content-type-options: nosniff, x-robots-tag: noindex, nofollow
GET /admin  /admin/  /admin/healthz    401  401  401
<h1>Ops dashboard</h1> on /admin/login: 1; on the portal's /login: 0
the sign-in form's action: "/admin/login"
```

`sentinel operator list` on the box: one operator, `john.great`, active,
2FA `on`, last login 2026-09-25 - no `NOT YET` row, so 22c item 1 holds.
The wrong-password log reading (`failed operator login for … from <the
operator's own address>`) was left for the owner to do from a browser;
nothing in the deploy depends on it.

**Step 9:** `SENTINEL_PORTAL_URL=https://guardian.minastrade.com` written
to `/opt/minas-guardian/.env`, `docker compose up -d` recreated sentinel
with it, `printenv SENTINEL_PORTAL_URL` → the address. From outside, once
the container was back: `GET /login` 200, `grep -c 'Our only address'` →
1, `grep -c 'Our address has changed'` → 0. (The first read, a second
after `up -d`, found 0 because the portal was still starting - read it
after the healthcheck, not before.)

**The restore order's step 3:** `systemctl enable --now
minas-liveness.timer minas-restart-on-unhealthy.timer` created both
symlinks and both timers fired at 09:17:52; root's crontab carries the
digest line live again (`0 8 * * * cd /opt/minas-guardian && docker
compose exec -T sentinel sentinel digest`) with the `# PAUSED 2026-09-28`
note left as a comment above it. #88 closed on this evidence.

**Still outside this entry:** the Tailscale admin-console removal of the
machine (a browser click); the email to members with the new address
(22d); the wrong-password log reading above.

*Later the same day, 12:12 UTC:* the email to members went - five portal
members, one plain-text message each, from support@minastech.ca; the text
and the review that shaped it are under 22d in docs/DECISIONS-NEEDED.md.

*Later again, 12:55 UTC:* a second operator account, `jimrox.odezi` (the
owner's, display name set), created at the owner's own terminal with
`sentinel operator add` and enrolled in two-factor; `sentinel operator
list` read afterwards: `jimrox.odezi active on`, `john.great active on` -
22c item 1 holds with two rows. The dashboard's log for that sign-in
carries the owner's real client address on the password step and on the
sign-in (not the proxy's 10.231.0.2), which is what step 8's
wrong-password reading was for; the failed-login line itself was not
produced, as no wrong password was entered. The enrolment pages showed
the secret as text only, and typing it into a phone failed once; the QR
code for them is its own change.

---

## 2026-10-23 · The box read before the public address: v1.12.0 on disk, the container stopped since 2026-09-28, Tailscale torn down (22e steps 4, 6 and 7)

No deploy. These are the readings that decided the order of
docs/deployment.md "The public address", and the teardown that step 4 of
it records. Every command was typed by the owner over SSH with the EC2
key pair at `57.181.51.14` (22e step 4: the key-pair login proven from the
network the rest was run from) and its output read by the session; the
address answered on the first try, which settles the stale
`ec2-54-168-212-44…` hostname question - the Elastic IP is current.

**Disk and container** (21:38 UTC): `git describe --tags` → `v1.12.0`,
checkout clean apart from `?? held/`. `docker compose ps` → no running
service; `docker compose ps -a` → `minas-guardian-sentinel-1 … Exited
(137) 3 weeks ago`; `docker inspect` → `exit=137 oom=false
finished=2026-09-28T20:48:42Z started=2026-09-22T12:26:51Z
restart=unless-stopped`; the Docker journal for that minute: `Container
failed to exit within 10s of signal 15 - using the force` - a `docker
stop`, not a crash, so the restart policy rightly left it down. Host up
109 days, no reboot. One SSH login that evening: `ubuntu` from
`105.112.176.210`, 2026-09-28 20:44-23:06 UTC. The container's last log
lines (20:46-20:48 UTC) are `breaker open, skipping account N` for nine
accounts. `held/README-2026-09-28.txt`, verbatim: "2026-09-28 20:50 UTC -
ALL ALERTING STOPPED (owner, beta, credit cost). Disabled:
minas-liveness.timer, minas-restart-on-unhealthy.timer (disable --now,
symlinks removed). Stopped: minas-guardian-sentinel-1 (Exited 137; PRAGMA
integrity_check = ok). Commented: root cron 'sentinel digest' 08:00 UTC.
Backup: root-cron-2026-09-28.bak in this folder. State at stop: 9
accounts dark 169.6h; 391 tamper alerts in prior 24h. RESTORE ORDER: 1)
nine exchange allowlists get 57.181.51.14 2) cut+deploy 1.13.0 3) then
start." Root's crontab carries the digest line commented out with a
"PAUSED 2026-09-28" note. The owner confirmed the allowlists (restore
step 1) done on 2026-10-23. This file had not recorded v1.12.0 or the
pause; the deploys between v1.7.0 and v1.12.0 are still unrecorded here.

**Why Caddy could not start on v1.13.0:** `ss -tlnp '( sport = :80 or
sport = :443 )'` → `tailscaled` LISTEN on `100.76.169.10:443` and the
Tailscale IPv6 address; `python3 -c "…socket().bind(('0.0.0.0',443))"`
with `SO_REUSEADDR` → `OSError: [Errno 98] Address already in use`.
v1.13.0 was cut (2026-10-23 14:40) and never deployed. Primary private
IPv4 `172.31.2.189/20` on `ens5`; `ip -4 route | grep '^10\.231\.'` →
nothing; volumes: `minas-guardian_sentinel-data` only.

**Teardown** (later that evening UTC; 22e steps 6 and 7, run before Caddy
for the reason above - nothing was serving, so nothing was lost):
`tailscale serve status` before → the four listeners of the 2026-09-25
reading (`:8443` Funnel → 8391; `:8391`, `/` → 8390, `:9109` tailnet-only).
`tailscale funnel reset; tailscale serve reset` → `serve status`: `No
serve config`. `tailscale down` → `tailscale status`: `Tailscale is
stopped.` `apt-get remove -y tailscale` → `Removing tailscale (1.102.2)`;
`apt-get purge -y tailscale` → `Purging configuration files`;
`rm -rf /var/lib/tailscale` (the node key: there is no coming back to the
old name). After, in a fresh session: `/usr/bin/tailscale` and
`/usr/sbin/tailscaled` → `No such file or directory`; `systemctl
is-active tailscaled` → `inactive`; `ss -tlnp | grep -c tailscaled` → `0`;
`ip -4 addr show tailscale0` → `Device "tailscale0" does not exist.`
(In the teardown shell `command -v tailscale` still printed the path: the
shell's command hash from the `tailscale status` run a moment earlier,
not the file.) Still to do in the Tailscale admin console: Machines →
minas-guardian → Remove.

**Step 5 of the runbook, read afterwards:** `held/` moved to
`/var/backups/minas-guardian/held-2026-09-28/` (both files listed there);
`ss` on 80/443 → no listener; `ip -4 route | grep '^10\.231\.'` → none;
`git status --short` in `/opt/minas-guardian` → nothing. The box is ready
for step 6, the deploy of the release that carries `/admin`.

---

## 2026-08-22 · v1.7.0 (current), and the four deploys this file missed

Discovered 2026-08-22 during an autonomous-run verification pass: the file's
newest entry was v1.5.1 while production ran v1.7.0. **Five deploys went
unrecorded** — the exact drift this file exists to prevent, caught this time
by measuring instead of trusting. Evidence, measured over read-only SSH:

The running container (`sentinel version --machine`, 2026-08-22):

```
version=1.7.0
commit=408ebde4a
tag=v1.7.0
source=build-arg
```

Disk agrees: `git describe --tags` → `v1.7.0`, HEAD `408ebde`.

The deploy history, from the production checkout's reflog (deploy.sh checks
the tag out itself, so each line is a deploy run):

| When (UTC, reflog) | Deployed | From |
|---|---|---|
| 2026-08-22 00:15:59 | **v1.7.0** | v1.6.2 |
| 2026-08-21 04:51:45 | v1.6.2 | v1.6.1 |
| 2026-08-21 03:27:13 | v1.6.1 | v1.6.0 |
| 2026-08-20 12:35:17 | v1.6.0 | v1.5.2 |
| 2026-08-19 01:22:09 | v1.5.2 | v1.5.1 |

Honest limits of this reconstruction: a reflog checkout proves deploy.sh
ran, not that its post-switchover version assertion passed — that
container-level evidence exists only for the version running now (v1.7.0,
above). v1.6.1's deploy is independently corroborated: the owner verified
it live (the first human portal sign-in, 2026-08-21). All deploys were the
owner's, per the standing rule.

---

## 2026-08-18 · v1.5.1 (from 1.4.2)

Deployed **2026-08-18 ~15:5x UTC** by the owner via
`sudo /opt/minas-guardian/scripts/deploy.sh v1.5.1` — the first run of the
tag-checkout deploy that this release itself ships, and the first deploy
whose final step **asserted** the version instead of reporting it.

### Before (measured by the deploy itself)

| What | Evidence |
|---|---|
| Container | deploy.sh "Currently running": **1.4.2** — commit `3419ffb97f6fa17264890150c985bbf13ce185b8`, tag `v1.4.2` |

### Deploy

| What | Evidence |
|---|---|
| Refusal first | The first attempt was **refused by the new dirty-tree check**: a DB backup sat inside the checkout. Filed and fixed as [#85] — backups now have a named home, `/var/backups/minas-guardian/`, and the refusal tells the untracked case where files belong |
| Method | `deploy.sh v1.5.1`: clean tree → `fetch --tags` → checkout `v1.5.1` (detached — the server now stays detached at the deployed tag by design) → gate → build → `validate-config` → switchover → healthy wait → version assert |

### After (asserted by the deploy, not read afterwards)

| What | Evidence |
|---|---|
| Container | `sentinel version`: **1.5.1**, commit `3cc4aee06448e5ae4ca93ce9c0174b463062ebb5`, tag `v1.5.1`, source **build-arg** — compared against the requested tag by the script itself, which exits non-zero on mismatch |

### Verified after deploy

- **The descriptor leak is closed in production** (#79 — the six-week
  outage this release shipped the fix for). Descriptor counts on the
  running container, measured by the owner (times UTC): **35 at 15:49**
  (immediately post-deploy), **30 at 16:05**, **30 at 20:46**. Five hours
  flat. At the pre-fix rate of ~11/min that window would have leaked
  ~3,300 descriptors and the container would have hit its 1024 ceiling and
  gone dark around 17:20 — as it had after every restart since early July.
  This is the measurement that closes the outage, not merely the fix that
  should have.

### v1.5.0: tagged, CI-green, never deployed

Its deploy was refused by the gate — HEAD was the post-tag log-handoff
commit, which is exactly the defect v1.5.1 fixes — and the refusal was
abandoned rather than worked around: no hand deploy happened. (A session
note claiming one is corrected inline in docs/AUTONOMOUS-LOG.md; the claim
had no entry in this file, which is the tell this file exists to provide.)
v1.5.0's changes — the #79 descriptor-leak fix among them — first reached
production inside this deploy, 1.4.2 → 1.5.1 in one step.

[#85]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/85

## 2026-08-18 · v1.4.2 (from 1.1.0)

Deployed **2026-08-18 ~01:06 UTC** by the owner via
`sudo /opt/minas-guardian/scripts/deploy.sh` (the script's first production
run). One step, four releases: 1.1.0 → 1.4.2.

### Before (measured on the server)

| What | Evidence |
|---|---|
| Container | `sentinel version`: **1.1.0**; commit, tag and source all **"unknown"** — built before version stamping existed |
| Disk | `git describe --tags`: **v1.1.0-2-g7561627** — two commits past the v1.1.0 tag, i.e. a non-release commit was live |

That row is this file's reason to exist: the container could not say what
it was, the disk was off the last release, and the docs said "the July
build" — three answers, none checked against each other for six weeks.

### Deploy

| What | Evidence |
|---|---|
| Backup, taken first | `/opt/minas-guardian/sentinel_data.backup-2026-08-18` (**349 MB**), copied from the Docker volume `minas-guardian_sentinel-data` |
| Method | `scripts/deploy.sh`: pull → gate (accepted `3419ffb`, exactly tag `v1.4.2`) → build → `validate-config` → switchover → healthcheck wait |

### After (measured on the server)

| What | Evidence |
|---|---|
| Container | `sentinel version`: **1.4.2**, commit `3419ffb97f6fa17264890150c985bbf13ce185b8`, tag `v1.4.2`, source **build-arg** |
| Health | **healthy** (the healthcheck state, not merely "started") |

### Verified after deploy

- **The pruner works in production.** First retention run deleted **190
  rows** (`balance_snapshots`); the next cycle deleted **95 more**. This is
  the repaired deletion job (1.4.0, `232528d`) doing its first real work.
- **The retention boundary reading, owed since the v1.4.0 handoff, is
  taken.** `retention-boundary.py` (run mounted — see [#77]): oldest data
  anywhere is **2026-07-02**. Events and alerts have **684 days** of
  headroom against their 730-day windows; the dedup tables **319 days**
  against 365. This **confirms by measurement** the previously derived
  claim that the foreign-key pruning defect destroyed nothing — the
  earliest possible boundary crossing was 2028, and the oldest surviving
  row is from the product's first week.
- **Email is configured and proven end to end.** Resend SMTP: a raw SMTP
  test was accepted, then `sentinel digest --hours 24` delivered a real
  digest with live account data to support@minastech.ca and to Telegram.
  The old Resend API key was rotated and deleted.

### What the deploy surfaced (filed, not fixed — deploy night is for recording)

| Issue | Finding |
|---|---|
| [#74](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/74) | `paula-garavillas`: **5,668 consecutive OKX poll failures**, "API key doesn't exist". *Corrected 2026-08-18: a **test account**, not a customer — reclassified p1 → p3; the fix is to disable it (offboarding is disable, never delete, G-50). It still matters because a warning every 45 seconds forever trains operators to ignore poll-failure warnings* |
| [#75](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/75) | SMS unconfigured (ClickSend/Twilio) while accounts carry phone contacts — CRITICAL now reaches Telegram **and email**; the SMS leg is still routed and absent |
| [#76](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/76) | Ops dashboard bound to **0.0.0.0 with no login of its own**; the tailnet carries ten devices across five people — establish who can reach port 8390 |
| [#77](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/77) | `scripts/` is not in the Docker image, so `retention-boundary.py` runs only mounted — contradicting v1.4.1's own release note; fix the Dockerfile or correct the note |
| [#78](https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/78) | `balance_snapshots` reported **PAST 0d** against its 7-day window minutes after a successful prune — probably day-granularity rounding at the boundary, not a backlog. Re-run and check whether the oldest date advances before deciding; if it is a false alarm, fix the report — a tool built to be trusted crying wolf on its first production run is the mutation-gate problem again |

[#77]: https://github.com/erwingarcia1/Minas-AI-Guardian-Bitget-Read-only/issues/77
