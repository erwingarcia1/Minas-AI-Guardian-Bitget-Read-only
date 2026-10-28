---
name: ui-parity
description: >-
  Carry a user-interface change to every surface that shows it - the member portal (web, sentinel/portal.py),
  the ops dashboard (web, sentinel/dashboard.py) and the iOS/Android app in mobile/ - and run the parity guards.
  Use this skill whenever a change touches anything a member or an operator sees, reads or can do, on ANY surface,
  even when the user mentions only one of them: sentinel/portal.py (markup, _PORTAL_STYLE, the copy constants),
  sentinel/dashboard.py, sentinel/brand.py, sentinel/static/, design/**, mobile/src/**, mobile/app.json,
  sentinel/api.py, api/openapi.yaml, sentinel/alerts/base.py (the customer-facing advice copy). That includes copy
  and wording edits, colours and tokens, the logo and icons, a new screen or page, routes and redirects, the
  sign-in flow and session handling, error and empty-state messages, notices, buttons, navigation, status words,
  the limits text and the landing page. If the request says portal, web UI, dashboard, mobile, app, screen, page,
  landing, login, sign-in, copy, wording, colour, token, logo, icon, brand or design, read this skill before
  editing: the web and the app must say and show the same thing wherever both have the surface.
---

# A UI change lands on every surface

The owner's rule (2026-10-24, decision 25): "when a change is made to the UI,
the change should reflect in both web UIs and the mobile UIs when necessary."
The app's screens are the portal's pages in the portal's words, carried by
the API. Tests pin some shared sentences; the rest is this skill. Work through
the six steps below in order and do not finish while a step is open.

## 1. The surface map

Find the row for what you changed. Everything in the same row changes together.

| Member sees | Portal (web, `sentinel/portal.py`) | App (`mobile/src/app/`) | Carried by |
|---|---|---|---|
| Landing: what the Guardian is, how to sign in | `GET /` without a session: `_render_landing`, `LANDING_STATEMENT`, `LANDING_LEAD`, `LANDING_SETUP`, `LANDING_STEPS`, `LANDING_ENDED_TITLE` | `landing.tsx` (route `/landing`) | copies of the constants; `tests/test_mobile_app.py::test_the_apps_landing_says_what_the_portals_does` pins them |
| Sign-in, code, forgot, reset, accepting an invitation | `/login`, `/login-code`, `/forgot`, `/reset`, `/welcome`: `_render_login`, `_render_code_prompt`, `_render_forgot`, `_render_reset_form`, `_render_welcome` (decision 17, 2026-10-28) | `sign-in.tsx` + `mobile/src/auth/session.tsx`, `tokens.ts` (mechanics fixed by decision 21c); `landing.tsx` and `sign-in.tsx` open `/welcome` in the browser | `POST /api/v1/session`, `/session/refresh`, `DELETE /session` |
| Home and "Am I being watched?" | `/` and `/status`: `_render_home`, `_render_status` | `(tabs)/index.tsx` | `GET /api/v1/accounts` |
| Alert history | `/alerts`: `_render_alert_history` | `(tabs)/alerts.tsx` | `GET /api/v1/alerts` |
| Incident: evidence, what to do next, acknowledge | `/incident`: `_render_incident`, advice from `sentinel/alerts/base.py` (`advice_steps`, `advice_lead`, `advice_note`) | `alert/[id].tsx` | `GET /api/v1/alerts/{id}` (its `advice` object), `POST /api/v1/alerts/{id}/acknowledge` |
| Security: two-factor, signed-in phones, sign out | `/security`: `_render_security` | `(tabs)/settings.tsx` | `GET /api/v1/security`, `DELETE /api/v1/devices/{id}`, `DELETE /api/v1/session` |
| Who has touched your accounts | `/activity`: `_render_activity` | `activity.tsx` | `GET /api/v1/activity` |
| What the Guardian can and cannot do | `/limits`: `_render_limits`, `LIMITS_HEADLINE`, `LIMITS_DOES`, `LIMITS_CANNOT`, `LIMITS_LEAD`, `LIMITS_WHY` | `limits.tsx` | `GET /api/v1/limits` (`sentinel/api.py` `_limits`) |
| Colours and type | `_PORTAL_STYLE` `:root` tokens, hues from `sentinel/brand.py` | `mobile/src/theme/tokens.json`, `mobile/src/theme/index.ts` | `tests/test_mobile_app.py::test_the_apps_colours_are_the_portals` holds them equal |
| Primitives | `_PORTAL_STYLE`: `.panel .badge .notice .btn .kv .steps .feature-list .phish .callout-danger` | `mobile/src/ui/index.tsx`: Screen, Title, Eyebrow, Body, Card, Section, Badge, KV, Button, Notice | `design/portal/DESIGN-SYSTEM.md` and `docs/portal-design.md` are the contract |
| The logo and icons | `sentinel/static/` (served at `/brand/*`), `sentinel/brand.py` markup | `mobile/assets/images/`, named by `mobile/app.json` | `design/brand/minas-logo.png` is the master; `scripts/brand-assets.py derive` writes both; `tests/test_mobile_app.py` pins the copies byte for byte |
| The ops dashboard | `sentinel/dashboard.py` (`/admin`), `design/dashboard/DESIGN-SYSTEM.md` | none: web-only | - |
| Print sheet, exports, the primary-only settings | the status page's `@media print`, `/export.json`, `/alerts.csv`, `/account/name`, `/key`, `/pause/*`, `/snooze`, `/expected`, `/preferences`, `/digest/frequency`, `/telegram/*`, `/drill`, `/erase` | none: portal-only by decision 21d (v2, one at a time; `/key` stays web-only) | - |
| Push notifications | none | app-only; open on decision 21e | nothing yet: the route arrives with 21e |

Also in the map, on the design side: `design/portal/` (the mockups and
`styles.css`, ported into `_PORTAL_STYLE`), `design/dashboard/` (the
dashboard's `_STYLE`), `design/brand/` (the master), and `design/` as a
whole, which describes a surface and never replaces one. The API side:
`sentinel/api.py` answers in the portal's words (it imports the portal's
constants and queries), `api/openapi.yaml` is the contract, and
`mobile/src/api/schema.d.ts` is generated from it. The rest of the app:
`mobile/src/auth/` (sign-in mechanics), `mobile/src/api/` (the client),
`mobile/src/` for anything new, and `mobile/assets/` (derived files only).

## 2. Decide what the change touches

1. Ask: does the change alter what a member sees, reads or can do? A refactor
   that renders the same bytes does not; a new sentence, colour, control,
   route, redirect, notice, error message or screen does.
2. If yes, name the rows of the map it lands on. One change often lands on
   two: a new limits sentence is a portal row and an API row.
3. Ask whether the API contract carries it. If the app reads the changed
   thing through `/api/v1/`, change `api/openapi.yaml`, then
   `cd mobile && npm run gen:api` and commit `mobile/src/api/schema.d.ts`,
   then make `tests/test_api_contract.py` pass. Never edit `schema.d.ts` by
   hand; CI regenerates it and fails on a difference.
4. If the app shows a copy of the words (the landing, the limits headline),
   change the TSX too, verbatim, and keep the pin in `tests/test_mobile_app.py`
   green. The strings are copies on purpose: the app cannot read the server
   before sign-in.
5. If the row says "none", the change is single-surface. Go to step 6.

## 3. The rules every surface obeys

- No JavaScript on the web beyond `_LOCAL_TIME_SCRIPT` and the incident
  page's print button (decision 1 on #82). No framework, no external request.
- One gold `.btn--primary` per screen; in the app, one primary `Button`.
- Status is a dot and a word, never colour alone (`.badge`; `Badge`).
- No prevention claim reaches a customer (G-44, #48): the product watches,
  reads, tells, alerts, keeps the record. It never "protects", "prevents",
  "stops", "blocks", "freezes", "cancels" or "reverses".
  `tests/test_no_prevention_claims.py` scans every string literal in
  `sentinel/portal.py`; hold the app's copies to the same verbs by hand.
- Numbers come from the running configuration, never from copy:
  `polling_cadence(rules)`, `SESSION_IDLE_SECONDS`, `SESSION_TTL_SECONDS`,
  the API's `timing` object.
- The public address comes from the setting only (`portal_host()`,
  decision 22d): named as text, never a link, and the moved notice appears
  on any other host.
- Key/value rows, never tables. Body text 16px minimum. Touch floors: 48px
  for buttons and inputs, 44px for tertiary controls. Mobile-first, no
  motion, focus rings on everything. Every emitted class is styled in its
  own sheet (`tests/test_surface_styles.py`).
- The dashboard shares the portal's ground and brand constants and nothing
  else; its operations marking tells it apart (decision 24).
- There is no open signup (decision 17; the owner, 2026-10-28: sign-up is
  for Minas members, and an exchange key is added only by an admin during
  onboarding). A login comes from the invitation code Minas emails; the
  landing says so, and "Accept an invitation" is on both surfaces.

## 4. Run the guards, by surface

From the repository root. Every surface you touched owes its own line.

Always:

```bash
.venv/bin/ruff check sentinel tests
.venv/bin/python -m pytest -q tests/test_surface_styles.py tests/test_no_prevention_claims.py tests/test_mobile_app.py tests/test_portal_address.py
```

`sentinel/portal.py` changed: also the page tests that pin the markup
(`tests/test_portal.py`, `tests/test_portal_landing.py`,
`tests/test_incident_page.py`, `tests/test_get_is_read_only.py`,
`tests/test_csrf_origin.py`) and the design-guard probes, one run at a
time, alone in the tree (they patch `sentinel/` in place and take
`.mutation-probe.lock`):

```bash
.venv/bin/python -m pytest -q tests/test_portal.py tests/test_portal_landing.py tests/test_incident_page.py tests/test_get_is_read_only.py tests/test_csrf_origin.py
python3 scripts/mutation-probe.py scripts/probes/design-guards.json
```

`sentinel/dashboard.py` changed: `tests/test_surface_styles.py` (above) and
`tests/test_dashboard_prefix.py`, then the same probe run.

`sentinel/api.py` or `api/openapi.yaml` changed:

```bash
.venv/bin/python -m pytest -q tests/test_api_contract.py tests/test_api_routes.py tests/test_api_reads.py tests/test_mobile_app.py
cd mobile && npm run gen:api && git diff --stat src/api/schema.d.ts
```

`mobile/` changed (screens, theme, ui, auth, app.json):

```bash
cd mobile && npm run typecheck && npm test -- --ci && npm run audit:check
cd mobile && npx expo export --platform ios --platform android --output-dir /tmp/claude-1000/expo-export-check && rm -rf /tmp/claude-1000/expo-export-check
```

`sentinel/brand.py`, `design/brand/`, `sentinel/static/` or
`mobile/assets/` changed: never hand-edit a derived file. Run
`python3 scripts/brand-assets.py derive` (needs Pillow), commit what it
writes, then `tests/test_mobile_app.py` and `tests/test_brand_assets.py`.

`sentinel/alerts/base.py` advice changed:
`tests/test_incident_advice.py`, `tests/test_incident_page.py`,
`tests/test_api_contract.py`, and the app's `alert/[id].tsx` reads the
`advice` object unchanged.

Red is a stop condition. Do not start the next piece of work until green.

## 5. Write the records

- `CHANGELOG.md` `[Unreleased]`: what the member sees first, then the
  mechanism, with the decision number. One entry covers both surfaces when
  both changed; say so.
- `mobile/CHANGELOG.md` `[Unreleased]` when the app changed (the app takes
  its own `mobile-vX.Y.Z` tags).
- `docs/portal-design.md` when a page, primitive or frame rule changed;
  `design/portal/DESIGN-SYSTEM.md` plus a mockup under `design/portal/` for a
  new screen; `design/dashboard/DESIGN-SYSTEM.md` for the dashboard.
- `mobile/README.md` "What it does" when a screen or its API changed.
- `docs/DECISIONS-NEEDED.md` when the owner decided something: the section,
  the summary-table row, and "Open today" if it moved.
- `docs/threat-model.md` when a sign-in, session or address rule moved.

## 6. What "when necessary" means

A change is web-only or app-only only when the map says the surface has no
counterpart: the ops dashboard; the print sheet, the exports and the
primary-only settings until API v2 (decision 21d); push notifications
(21e). In every other row the other surface changes in the same piece of
work, or the work is not done. When a change is single-surface, the commit
message says which row and why ("portal-only: the print sheet has no app
counterpart, decision 21d"), so the next reader does not go looking for the
missing half. Never decide single-surface from convenience, scope or the
request naming one surface: the owner's rule is the default, and the map is
the only exception list.
