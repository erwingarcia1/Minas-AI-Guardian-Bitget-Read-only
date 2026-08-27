# Ops dashboard — design decisions (#82, the dashboard half)

The owner's decision on #82 (2026-08-19): the portal carries the Minas
identity; the ops dashboard is **plain, dense, unbranded** — no mark, no
wordmark, no gradients, no glow, none of the identity hues. An ops
screenshot must not be usable to impersonate a customer page, and a
mispasted internal URL should look visibly wrong to whoever receives it.
Distinct looks are part of the anti-phishing story, not aesthetics.

Server-rendered HTML + CSS, one stylesheet (`_STYLE` in
`sentinel/dashboard.py`), no framework. The one standing JS exception is
`_LOCAL_TIME_SCRIPT` (local-time upgrade of UTC timestamps; the page works
without it). Sort and filter are server-side query parameters (#39).

## How it is distinct from the portal, on purpose

| | Member portal (`design/portal/`) | Ops dashboard |
|---|---|---|
| Ground | dark `#0d0f13` | light `#f4f5f7` |
| Identity | infinity mark, gold, violet, "Member portal" lockup | none — "Ops dashboard · internal · Minas staff only" in plain text |
| Accent | gold primary buttons, violet links/focus | one utilitarian navy `#1f4e79` for links, buttons, focus |
| Shape | rounded cards, generous spacing, 16px body | 2–3px radii, dense rows, 13–14px body, tabular numerals |
| Tone | reassuring, one answer per screen | a control surface: tables first, everything on one screen |

`tests/test_surface_styles.py` refuses the convergence: the two sheets are
separate, the dashboard's contains no brand hue, no gradient and no
wordmark, the portal's lockup never appears in the dashboard, and every
class the dashboard emits is styled by its own sheet.

## Colour roles

| Token | Value | Role |
|---|---|---|
| `--bg` / `--panel` / `--panel-2` | `#f4f5f7` / `#fff` / `#eef0f3` | page / cards / card heads, inputs |
| `--line` / `--line-soft` | `#c9ced6` / `#e2e5ea` | outlines / row dividers |
| `--txt` / `--muted` / `--faint` | `#16191d` / `#4b5563` / `#6b7280` | primary / secondary / labels |
| `--accent` | `#1f4e79` | links, primary buttons, focus outline |
| `--crit` / `--high` / `--med` / `--low` / `--ok` | `#b91c1c` / `#b45309` / `#a16207` / `#6b7280` / `#15803d` | severity and state, always with a text label |

All pairs clear WCAG AA on white. Status is never colour alone: pills carry
the word, checks carry a glyph.

## Type and spacing

System font stack; body 14px, table cells 13px, labels 11px uppercase
tracked; monospace for rules, ids and the build line. Spacing is tight
(6–12px paddings): this page is scanned by an operator who knows it, not
read by a frightened customer.
