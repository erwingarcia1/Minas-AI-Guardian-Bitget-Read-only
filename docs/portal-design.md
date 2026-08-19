# The portal design foundation

The member portal's design system: tokens, primitives, and the rules that
keep ten screens looking like one product. Built as one piece (#82) before
any screen issue, so screens **inherit rather than reinvent**. The
stylesheet itself is `_PORTAL_STYLE` in `sentinel/portal.py`; this file is
the reasoning and the contract.

## The decisions this implements (owner, 2026-08-19 — recorded on #82)

1. **No framework.** Plain server-rendered HTML + CSS. The no-JS posture is
   security posture, not habit. The one standing exception:
   `_LOCAL_TIME_SCRIPT`, a ~10-line inline enhancement upgrading
   server-rendered UTC timestamps to local time — fully degrading, no
   requests, no input. Anything beyond that shape is a decision, not a diff.
2. **The portal carries the Minas brand; the ops dashboard is deliberately
   plain, dense, unbranded** (its stripping happens in its own pass, which
   waits on the access model — #87). An ops screenshot must not be usable to
   impersonate a customer page, and a mispasted internal URL should look
   visibly wrong.
3. **Brand assets live in `sentinel/brand.py`** — the gold/violet identity
   and the infinity mark, one home, so the brand cannot drift into two
   spellings of gold.
4. **Interface over presentation.** The shared ancestor stylesheet was
   presentation-oriented: glow gradients, a pulse animation, gradient
   panels. A customer opens this page frightened at 3am, and it is printed
   as evidence — so the portal keeps the brand's dark ground and drops the
   effects. No animation anywhere. The one decorative element is the **gold
   hairline at the top of the page**: the portal's signature, absent from
   the dashboard on purpose.

## Tokens (all in `:root`, all with role names)

| Group | Tokens | Rule |
|---|---|---|
| Ground | `--bg --surface --surface-2 --line --line-soft` | Panels are flat `--surface`; raised elements (inputs, hover) use `--surface-2`. No gradients. |
| Ink | `--ink --ink-muted --ink-faint` | Three steps only. Body text is `--ink-muted` with `--ink` for the part that matters (`.txt b` pattern). |
| Brand | `--gold --accent` | Gold is for **identity moments only**: the wordmark, the portal tag, the primary action, the hairline. Violet (`--accent`) is for interaction: links, focus, hover borders. Neither ever conveys status. |
| Status | `--ok --warn --crit --med --low` | Status colours are reserved for status. If it isn't a state, it doesn't get one. |
| Type | `--t-display --t-title --t-body --t-small --t-micro` | Five sizes. A screen that wants a sixth is wrong. |
| Space | `--s1..--s7` (4-48px on a 4px base) | No raw pixel margins/paddings in new rules. |
| Shape | `--radius --radius-pill` | |

## Primitives (one of each — screens compose, never fork)

- **The table** — bare `<table>` inside `.card`; micro-caps `thead`, roomy
  rows, `--surface-2` hover. There is one table style; a screen that needs a
  second kind of table needs a design conversation instead.
- **The pill** — `.pill` plus a role modifier (`.s-active .s-disabled
  .s-tamper .sev-*`). New status → new role class here, never an inline
  colour.
- **The banner** — `.banner` plus tone (`.ok .warn .err`). One banner style
  for everything page-level: errors, confirmations, the not-watched warning.
- **Check rows** — `.checks > .check` with `.ic` tone circles: the posture
  and status statements ("Read-only access ✓").
- **Forms** — `.fld` (micro-caps label + input) inside `.card`, `.actions`
  with the single gold `.submit`. Quiet controls (`.ackbtn`, `.snoozesel`)
  stay bordered-transparent: a list of things that happened must not read
  as a page asking to be clicked.
- **Definition list** — `.dl` for label/value detail panes.
- **Print** — the `@media print` block is load-bearing: incident pages are
  evidence handed to exchanges and police (export is the browser's own
  print-to-PDF, decision 5 in DECISIONS-NEEDED.md). Test any new primitive
  in print preview; `.noprint` hides interactive chrome.

## The rules

1. **No hex outside `:root`** (rgba() alpha variants of tokens excepted).
   A new colour is a new token with a role name, added here with reasoning.
2. **Every emitted class is styled** — enforced, not requested:
   `tests/test_surface_styles.py` fails when portal markup emits a class
   its own stylesheet never styles (the registration/execution pair,
   applied to CSS).
3. **The two surfaces never share a stylesheet** — the same test file
   refuses `_STYLE` imports in either direction and asserts each surface
   keeps its distinguishing marks. The portal and dashboard looked
   identical for months because one stylesheet quietly served both; that
   cannot recur silently.
4. **Screens inherit.** A screen issue (#34, #10, #22, #20, #9, #45, #31,
   #18, #21) should add markup that composes the primitives above. If it
   needs a new primitive, the primitive lands here first, documented, in
   its own commit.

## Consequences recorded elsewhere

- **#39** (dashboard sort/filter): must be server-side — query parameters
  and re-render. No client-side table JS on either surface.
- The dashboard's own design pass (plain, dense, unbranded) waits on its
  access model: #87's 2FA policy, and the fact that production has zero
  operator accounts today.
