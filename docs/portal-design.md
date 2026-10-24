# The portal design system

The member portal's design system: tokens, primitives, and the rules that
keep every screen looking like one product. **The reference is the owner's
external design pass in `design/portal/`** — `DESIGN-SYSTEM.md`, `styles.css`
and five mockup screens (signin, accounts, status, limits, alert), produced
2026-08-19 and ported into `_PORTAL_STYLE` in `sentinel/portal.py` as a
system, not a paste: mockup-only artifacts (state-variation reference
sections, a remember-me checkbox) were dropped, and utilities the real pages
need are defined from the same tokens. Screens **inherit rather than
reinvent**; this file is the contract, `design/portal/DESIGN-SYSTEM.md` is
the fuller design rationale.

## The decisions in force (owner, 2026-08-19 — recorded on #82)

1. **No framework.** Plain server-rendered HTML + CSS; the no-JS posture is
   security posture. Standing exceptions, named: `_LOCAL_TIME_SCRIPT` (UTC
   timestamps upgraded to local time; degrades cleanly) and the incident
   page's `window.print()` button. Anything beyond needs a decision, not a
   diff. **Consequence for #39**: dashboard filtering is server-side.
2. **The portal carries the Minas identity; the ops dashboard stays plain,
   dense, unbranded** (its pass shipped in v1.10.0, #82;
   `design/dashboard/DESIGN-SYSTEM.md` is the dashboard's contract). An ops
   screenshot must not be usable to impersonate a customer page.
3. **Brand assets live in `sentinel/brand.py`**: the gold/violet identity
   and the Minas logo (supplied 2026-10-22, replacing the stroked infinity
   mark). The logo is raster, derived from one master
   (`design/brand/minas-logo.png`) by `scripts/brand-assets.py`, and served
   by the portal itself from `sentinel/static/` - still no external
   request.
4. **Portal first**; the dashboard's pass followed in v1.10.0, under
   `design/dashboard/DESIGN-SYSTEM.md`.

## Tokens (`:root`, all with role names)

- **Surfaces**: `--bg --surface --surface-2 --border --border-strong` —
  dark, flat, no gradients, no glow. Colour is functional, not decorative.
- **Text**: `--text --text-muted --text-subtle`, plus `--text-on-gold`.
- **Identity**: `--gold` (the one primary button per screen, the
  active-nav underline — used sparingly) and `--violet`/`--violet-text`
  (links, secondary outlines, focus rings; the light variant clears AA for
  small text).
- **Status**: `--danger --warn --ok`, each with `-bg` and `-line` variants.
  Status is never conveyed by colour alone — always a dot + text label
  (`.badge`), so meaning survives print and colour-blindness.
- **Type**: `--text-xs/sm/base/lg/xl/2xl/display`; body is 16px minimum —
  read on a phone at 3am, not a marketing page. Print switches to serif.
- **Space**: `--space-1..8` (4px base, rem). **Radius**: 6px/4px.
- **Layout**: `--container: 44rem` — a short reading line, one fact in
  focus, on any device.

## Primitives (one of each — screens compose, never fork)

- **`.kv` key/value rows — THE answer to tables.** The data is key/value
  facts; below 40rem each pair stacks (uppercase micro-label above value),
  from 40rem it becomes a `12rem / 1fr` row. **There are no `<table>`s in
  the portal and horizontal-scroll tables must not be mixed back in** — the
  guard refuses both. Chosen over scroll because it is the most legible at
  3am on a phone, the rows are facts a frightened user scans one at a time,
  and it prints cleanly.
- **`.acct` account cards** — label + exchange + status badge, then `.kv`
  rows, then an optional `.problem` callout with an explicit fixability row
  (`.fix-tag` "You can fix this" + action, or muted "We're on it").
- **`.badge`** — status and severity both; dot + word, `--lg` for the alert
  headline badge.
- **`.state` banner** — the status page's one-sentence answer: eyebrow with
  icon, display-type answer, one clarifying line; ok/warn/danger tones.
- **`.panel`**, **`.notice`** (danger/info/ok, with icon), **`.callout-danger`**
  (the unmissable "cannot stop an attack" block), **`.feature-list`** with
  tick icons, **`.steps`** (numbered what-to-do-now), **`.phish`** (the
  anti-phishing note).
- **Buttons**: `.btn--primary` (gold fill, dark text — **exactly one per
  screen**), `.btn--secondary` (violet outline), `.btn--tertiary` (quiet
  text, still 44px). `.btn--sm` (40px) only for non-critical card-level
  actions, **never alert acknowledgement** — guarded.
- **Forms**: `.form > .field > .input` (48px), labelled, `aria-invalid` on
  errors, hints under fields.
- **The frame**: `.topbar` (brand + signed-in meta) over a horizontally
  scrollable `.subnav` with the current page underlined in gold
  (`aria-current="page"`); `.footer`; `.skip-link`; auth pages use the
  centered `.auth-card` shell.

## Mobile and touch

- **Mobile-first at 375px**; enhancement breakpoints at 40rem and 48rem
  (min-width only — the guard refuses max-width queries).
- **Touch floor 48px** (`.btn`, `.input`, `.subnav a`); 44px for tertiary
  and link-shaped controls. Guarded.
- **One primary per screen**, gold, placed in `.primary-row` directly under
  the page's main statement — reachable without scrolling. **Exception,
  deliberate**: the alert detail must be read before acting, so acknowledge
  lives in `.actionbar` — fixed to the bottom on phones (safe-area aware),
  inline at desktop.
- **No motion anywhere**; `prefers-reduced-motion` honoured explicitly.
  Focus is a 3px violet ring on everything, including the gold button.

## Print (status page = a document)

`@media print` strips chrome, flips to serif monochrome, reveals the
`.print-masthead` (report title, account, generation time, and what the
report does not prove), suppresses badge fills so status reads as words,
and avoids breaking cards across pages. Export stays the browser's own
print-to-PDF (decision 5): no PDF dependency, and the browser's URL/time
footer is better provenance than a composed document.

## The rules

1. **No hex outside `:root`** (rgba alpha variants of tokens excepted).
2. **Every emitted class is styled** — enforced by
   `tests/test_surface_styles.py`, along with: no shared stylesheet between
   portal and dashboard (either direction), the brand stays in `brand.py`,
   gold fill reserved for the primary, the touch floors, mobile-first,
   no tables, and no motion.
3. **Screens inherit.** A screen issue (#34, #10, #22, #20, #9, #45, #31,
   #18, #21) composes these primitives; a new primitive lands here and in
   the stylesheet first, in its own commit, checked against
   `design/portal/` for spirit.
