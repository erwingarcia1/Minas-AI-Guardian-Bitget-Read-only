# Ops dashboard — design decisions (#82, the dashboard half; decision 24)

The ops dashboard carries the Minas identity, dark like the member portal,
as the **Operations** surface (the owner, 2026-10-24; decision 24 in
docs/DECISIONS-NEEDED.md). ~~The owner's decision on #82 (2026-08-19): the
ops dashboard is plain, dense, unbranded — no mark, no wordmark, none of
the identity hues, one utilitarian navy `#1f4e79`, a light ground against
the portal's dark one.~~ - **retired by decision 24 (2026-10-24):** the
owner wants the Minas colours and logo on every dashboard page. What that
decision protected - an ops screenshot must not pass for a customer page,
and a mispasted internal URL should look visibly wrong - is kept by an
explicit operations marking, not by colour.

Server-rendered HTML + CSS, one stylesheet (`_STYLE` in
`sentinel/dashboard.py`), no framework. The one standing JS exception is
`_LOCAL_TIME_SCRIPT` (local-time upgrade of UTC timestamps; the page works
without it). Sort and filter are server-side query parameters (#39). The
identity constants come from `sentinel/brand.py`, as the portal's do: the
sheet spells each hex in its `:root`, and the guard holds them equal.

## The operations marking (on every page, signed in or not)

With the ground shared, these four tell an operator screen from a
customer page:

1. The lockup: the Minas mark, the wordmark "Minas AI Guardian" and the
   qualifier **Operations** in its own class `ops__tag` - never the
   portal's `brand__tag`, whose word is "Member portal".
2. The signed-in operator's name and a sign-out control in the header
   ("Signed in as erwin · Sign out"); the sign-in family shows the lockup
   without a name.
3. `<h1>Ops dashboard</h1>` and "internal · Minas staff only" in the
   header; the title `Ops dashboard — internal` and the footer sentence
   are byte for byte what they were (the deploy runbook greps the h1).
4. The path: everything lives under `/admin`, and the title says
   "internal".

No customer-facing copy anywhere in the shell: no tagline, no limits
link, no reassurance sentence, never the words "Member portal"; the word
"Accounts" never appears on a pre-session page.

The logo is the portal's `/brand/mark.png` on the shared host, reached
through `brand.MARK_HTML` and `brand.ICON_LINKS` as they are (no
dashboard asset route); through the SSH tunnel, where only the dashboard
answers, the image does not load and nothing else changes - it is
decorative (`alt=""`).

## Portal and dashboard, side by side

| | Member portal (`design/portal/`) | Ops dashboard (Operations) |
|---|---|---|
| Ground | dark `#0d0f13` | the same dark `#0d0f13`, on purpose |
| Identity | mark + "Minas AI Guardian" + "Member portal" tag | mark + "Minas AI Guardian" + **"Operations"** tag + `<h1>Ops dashboard</h1>` + the operator's name and sign-out |
| Accent | gold primary, violet links | the same family: the one gold primary per screen ("+ Add customer"), deep violet `#6d28d9` buttons, violet-text links |
| Shape | rounded, generous, 16px body | the portal's radii (6px cards, 4px controls) at the dashboard's density: dense rows, 13–14px body, tabular numerals |
| Tone | reassuring, one answer per screen | a control surface: tables first, everything on one screen, no customer copy |

## Colour roles

Every hex is spelled in `_STYLE`'s `:root` and nowhere else but
`@media print`; `<meta name="color-scheme" content="dark">`, like the
portal.

| Token | Value | Role |
|---|---|---|
| `--bg` | `#0d0f13` | page ground, the portal's |
| `--panel` | `#151921` | cards, tables |
| `--panel-2` | `#1c212b` | card heads, row hover, inputs |
| `--line` | `#2a3140` | outlines |
| `--line-strong` | `#3a4256` | input outlines, the `.ackbtn` outline |
| `--line-soft` | `#232a37` | row dividers |
| `--txt` | `#f4f6fa` | primary text (16.3:1 on panel, 17.7:1 on bg) |
| `--muted` | `#aab2c1` | secondary text, 11–12px labels (8.3:1 on panel) |
| `--faint` | `#8b94a7` | counts, placeholders, `--low` (5.8:1 on panel, 6.3:1 on bg) |
| `--brand` | `#2e1a47` | the header band and the sign-in card head; white text on it (15.5:1), gold on it (8.3:1) - `brand.VIOLET_INK` |
| `--brand-2` | `#583878` | band chip hover - `brand.VIOLET_LOGO` |
| `--violet` | `#8b5cf6` | focus ring (3px), the input focus border, the KPI hairline - `brand.VIOLET` |
| `--violet-text` | `#c4b2ff` | links, card-head `h2`, `a.acctlink:hover` (10.2:1 on bg; the portal's) |
| `--accent` | `#6d28d9` | `.submit` buttons and `.ackbtn:hover` ground, `--txt` on it (6.6:1) - `brand.VIOLET_DEEP` |
| `--gold` | `#f0b429` | the Operations tag, `::selection`, the critical KPI numeral, and the ONE gold-filled primary per screen with `--gold-ink` text - `brand.GOLD` (10.3:1 as text on bg) |
| `--gold-ink` | `#16110a` | text on any gold fill (10.1:1) |
| `--ok` | `#34d399` | watched / active / passed |
| `--crit` / `--bad` | `#f87171` | critical, tamper, failed, the triage overdue cell |
| `--high` | `#fb923c` | high severity (at least 7:1 on panel) |
| `--med` | `#fbbf24` | medium severity, warn |
| `--low` | `#8b94a7` | low severity (= `--faint`) |
| `--crit-bg` / `--crit-ink` / `--crit-line` | `rgba(248,113,113,.10)` / `#fecaca` / `rgba(248,113,113,.45)` | `.banner.err`; the bg and line on `.pill.s-tamper` and `.pill.sev-CRITICAL` |
| `--ok-bg` / `--ok-ink` / `--ok-line` | `rgba(52,211,153,.10)` / `#a7f3d0` / `rgba(52,211,153,.45)` | `.banner.ok`; the bg and line on `.pill.s-active` and `.pill.ack-yes` |
| `--high-bg` / `--high-line` | `rgba(251,146,60,.10)` / `rgba(251,146,60,.45)` | `.pill.sev-HIGH` |
| `--med-bg` / `--med-line` | `rgba(251,191,36,.10)` / `rgba(251,191,36,.45)` | `.pill.sev-MEDIUM` |

Every text token clears WCAG AA (4.5:1) against `--panel` and `--bg`.
Status is never colour alone: pills carry the word, checks carry a glyph.
Print flips to white paper and black ink, as before.

## Shape

The portal's family at the dashboard's density: 6px radius on cards and
the band's chips, 4px on inputs and buttons; 1px borders in `--line`;
card heads on `--panel-2` with `h2` in `--violet-text`, 12px, 700,
uppercase, .08em tracking; tables on `--panel`, header row 11px `--muted`
uppercase, rows divided by `--line-soft`, hover `--panel-2`; pills in the
portal's badge shape (a 6px dot + the uppercase word, 999px radius), the
class names unchanged; KPI tiles on `--panel` with a 2px `--violet` top
hairline, 26px tabular values in `--txt`, the critical value in `--gold`
when above zero. The header is a full-width band in `--brand`: the lockup
left, the h1 and sub after a hairline rule, the nav chips on the band
(hover on `--brand-2`), the operator's name far right. The enrolment QR
stays black on its own white tile.

Pills: the portal's badge shape and tones, the class names unchanged - a
ground and a border per tone from the `-bg`/`-line` tokens above; LOW and
disabled stay neutral.

## The guard (`tests/test_surface_styles.py`, the dashboard half)

What it refuses: `gradient`, `glow`, `@keyframes` and any motion, any
external request (`@import`, `url(http`), a shared stylesheet in either
direction, the portal's `brand__tag`, the words "Member portal" and any
`/limits` link in the shell, gold as a fill anywhere but the one primary
(`a.live.primary`) and `::selection`, and the retired `MARK_SVG`.

What it requires: the four marking parts on `_page`'s output (the
Operations lockup, the operator's name when signed in, the h1 with
"Minas staff only", the title); the brand hues present in the sheet and
equal to `brand.py`'s (`GOLD`, `VIOLET`, `VIOLET_DEEP`, `VIOLET_INK`, `VIOLET_LOGO`);
`MARK_HTML` and `ICON_LINKS` from `brand.py`, with no `/brand/` path
typed in `dashboard.py`; every class the dashboard emits styled by its
own sheet, `.brand__mark` included; AA for every text token against
`--panel` and `--bg`; and this note naming decision 24 and #82 with the
sheet's `--accent` and `--brand` values.

## The metrics page

`/admin/metrics` renders inside the dashboard shell (`_page`, 15-second
refresh): the service cards as `.kpis`, the runtime table as a `.card`.
The retired infinity mark, the radial gradients, the glowing tile, the
"MINAS" wordmark and the pulse are gone. The standalone 9109 page is a
different server with no brand files to point at: a text header, no
logo, no gradient, no motion.

## Type and spacing

System font stack; body 14px, table cells 13px, labels 11px uppercase
tracked; monospace for rules, ids and the build line. Spacing is tight
(6–12px paddings): this page is scanned by an operator who knows it, not
read by a frightened customer.
