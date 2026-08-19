# Minas AI Guardian — design decisions

Static HTML + CSS only. No JavaScript, no build step, no external requests.
These notes cover the foundation so additional screens can be built against it
consistently. One shared stylesheet (`styles.css`) links into every screen.

## Files

| File | Screen |
|------|--------|
| `signin.html` | Sign in (rendered in the **wrong-credentials error** state) |
| `accounts.html` | Home / My accounts (four-account case, plus a one-account reference) |
| `status.html` | Am I being watched? (all three states; print stylesheet) |
| `limits.html` | What the Guardian can and cannot do |
| `alert.html` | Alert detail (critical; sticky acknowledge) |
| `styles.css` | Shared design system |

---

## Colour roles

Dark surface, no gradients, no glow. Colour is functional, not decorative.

| Token | Value | Role |
|-------|-------|------|
| `--bg` | `#0d0f13` | Page background |
| `--surface` | `#151921` | Cards, top bar, auth card |
| `--surface-2` | `#1c212b` | Raised surfaces, inputs, numbered steps |
| `--border` / `--border-strong` | `#2a3140` / `#3a4256` | Dividers and outlines |
| `--text` / `--text-muted` / `--text-subtle` | `#f4f6fa` / `#aab2c1` / `#768093` | Primary / secondary / tertiary text |
| **`--gold`** | `#f0b429` | Identity. Infinity mark, primary buttons, active nav underline, and a few emphasis numerals. Used sparingly. |
| `--violet` / `--violet-text` | `#8b5cf6` / `#c4b2ff` | Interactive accents: links, secondary button outlines, **focus rings**. The lighter `--violet-text` is used for small link text to clear AA on the dark bg. |
| `--danger` | `#f87171` | Critical / attack / not-watched |
| `--warn` | `#fbbf24` | Partial / paused / needs attention |
| `--ok` | `#34d399` | Watched / active / resolved |

Status colours are always paired with a **text label and an icon** — never
colour alone — so meaning survives print, colour-blindness, and broken CSS.
Every status token has a matching `*-bg`, `*-line`, and badge variant.

All foreground/background pairs meet WCAG AA (most meet AAA). Notably the gold
button carries near-black text (`--text-on-gold`), and the critical red was
lightened to `#f87171` to clear AA normal text on the dark bg.

---

## Type scale

System font stack only:

```
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
             Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
```

| Token | Size | Use |
|-------|------|-----|
| `--text-xs` | 0.75rem / 12px | Eyebrows, labels, badge text (uppercase, tracked) |
| `--text-sm` | 0.875rem / 14px | Secondary text, hints, timestamps |
| `--text-base` | 1rem / 16px | Body and form controls (never smaller) |
| `--text-lg` | 1.125rem / 18px | Card labels, lead text, h3 |
| `--text-xl` | 1.375rem / 22px | h2 |
| `--text-2xl` | 1.75rem / 28px | h1 |
| `--text-display` | `clamp(1.75rem, 7vw, 2.25rem)` | The one-sentence answer on the status page |

Body stays at 16px — this is read on a phone at 3am, not a marketing page.
Headings are 700–800 with tight line-height (1.2); body is 1.55. In **print**
the document switches to a serif stack (`Georgia, "Times New Roman", serif`)
so it reads as a report, not a screenshot.

---

## Spacing scale

4px base, in rem:

```
--space-1  0.25rem   --space-5  1.5rem
--space-2  0.5rem    --space-6  2rem
--space-3  0.75rem   --space-7  3rem
--space-4  1rem      --space-8  4rem
```

- Page/section rhythm uses `--space-5` (mobile) and `--space-6`–`--space-7`
  between major blocks.
- Card padding is `--space-5`; internal gaps are `--space-3`/`--space-4`.
- The `.stack` / `.stack-lg` utilities give consistent vertical rhythm.

---

## Layout

- Mobile-first at **375px**, then a single enhancement breakpoint at
  **48rem (768px)** (plus a 40rem breakpoint for two-column key/value rows).
- Content is centered in a `--container: 44rem` (704px) column. This keeps the
  reading line short and the focus on one fact, on any device.
- Horizontal padding is 1rem on the phone, 1.5rem at desktop.
- Pages reflow at 200% zoom with no fixed-width containers and no horizontal
  scroll (the sub-nav is the only horizontally-scrolling region, and it's
  intentional and touch-scrollable).

### Header
Top bar holds the infinity mark + wordmark left and meta right. A second row
is the primary navigation (My accounts / Am I being watched? / Limits) with
the current page underlined in gold. The infinity mark is an inline SVG
(stroked, no fill) — same path reused everywhere.

---

## Buttons and touch targets

- **Minimum 48×48px** (above the 44px requirement) for every control; small
  secondary actions (`.btn--sm`) are 40px and used only for non-critical
  card-level actions, never alert acknowledgement.
- **Exactly one primary action per screen**: gold fill (`--gold`) with dark
  text, class `btn btn--primary`. Everything else is a violet-outline
  secondary (`btn--secondary`) or a quiet text tertiary (`btn--tertiary`).
- Placement is consistent: the primary action sits directly under the page's
  main statement / heading (`primary-row`) so it's reachable without scrolling
  past content on a phone.
- **Exception — alert detail:** the content must be read before acting, so the
  acknowledge action is in a fixed bottom bar on mobile (`.actionbar`,
  `position: fixed`) and flows inline at desktop. `<body class="has-actionbar">`
  reserves bottom space so content isn't hidden behind it.
- `:focus-visible` draws a 3px violet outline offset 2px on **every**
  interactive element, including the gold button (violet reads against gold).
- Disabled state: muted surface fill, `--text-subtle` text, `not-allowed`,
  `pointer-events: none`.

---

## Tables on mobile — decision: stacked label/value blocks

There are no data tables in this product; the account and alert data is a set
of key/value facts. The chosen approach, used **consistently on every screen**,
is the `<dl class="kv">` pattern:

- **Below 40rem:** each `dt`/`dd` pair stacks — small uppercase label above a
  full-width value. No pinching, no horizontal swipe.
- **At 40rem and up:** pairs become a `12rem / 1fr` two-column row.

This was chosen over horizontal scroll because (a) it's the most legible at
3am on a phone, (b) the rows are facts a frightened user scans one at a time,
and (c) it prints cleanly. If a genuinely tabular dataset is added later, use
the same stacked `<dl>` treatment — **do not mix in horizontal-scroll tables**.

Account lists are cards (`.acct`) — label + exchange + status badge at the top,
then the `kv` rows, then an optional `.problem` callout.

---

## States and status

### Status page banners (`.state`)
Three states, each with its own token set but identical structure — an
eyebrow with an icon, a one-sentence answer in display type, and one
clarifying line:

- `.state--ok` (green): **"You're being watched."**
- `.state--warn` (amber): **"Some of your accounts are not being watched."**
- `.state--danger` (red): **"Your accounts are NOT being watched."**

The **danger state is shown first** because it dominates the design — that's
the screen someone sees when things are wrong. The other two are included as
clearly-labeled reference sections (`.state-section.auxiliary`, hidden in
print).

Per-account problems (`.problem`) always give: **what's wrong in plain
language**, a sentence on the consequence, and a fix row that says explicitly
whether the customer can fix it (green `You can fix this` tag + action) or
can't (muted `We're on it — you can't fix this` + support link).

### Badges (`.badge`)
Active / Watched (ok), Suspected tampering / Key rejected / Unreachable
(danger), Disabled / Paused (neutral or warn). Each has a dot glyph + text
label.

---

## The "cannot stop an attack" message

On the limits page this is its own `callout-danger` block directly under the
page heading — a red-bordered panel with a 22px bold heading, before any other
content. It is not a footnote. The alert detail repeats the same constraint in
an info notice next to the acknowledge action.

---

## Sign-in anti-phishing line

A shield-prefixed note under the form states Minas never emails a link asking
for an API key and tells customers to open the site directly. The same warning
is reinforced in the password field hint. The **error state** renders a
`role="alert"` danger notice and marks both fields `aria-invalid="true"`.

---

## Print stylesheet (status page)

The status page is meant to be printed for an exchange, insurer, or police.
The `@media print` block:

- strips all chrome (top bar, nav, footer, action bar, buttons, variation
  sections, skip link);
- flips to white background, black text, serif type, and solid black borders;
- prints **only the primary (danger) state** — the `--warn` and `--ok`
  reference sections carry `.auxiliary` and are hidden;
- reveals a `.print-masthead` (hidden on screen) with the report title,
  account, generation timestamp, and a sentence about what the report does and
  doesn't prove;
- suppresses badge dots and fills, so statuses read as text labels, not colour;
- avoids breaking cards/banners across pages (`break-inside: avoid`);
- sets `@page { margin: 18mm 16mm }`.

Other pages also lose their interactive chrome when printed, but only the
status page carries the document masthead.

---

## Accessibility checklist

- Real headings (`h1`–`h3`), real lists, real `<dl>`/`<dt>`/`<dd>`, real
  `<form>` with associated `<label>`s, `autocomplete` on credentials.
- Skip link, `aria-current` on active nav, `role="alert"`/`role="status"` on
  error and state banners, `aria-label` on the brand link.
- Visible 3px focus ring on all interactive elements.
- Status never conveyed by colour alone — always icon + word.
- AA contrast across text/background pairs; 16px minimum body/control text.
- No motion anywhere; `prefers-reduced-motion` explicitly honoured.
- Reflows at 200% — no fixed px widths on content, no horizontal overflow.
- Touch targets ≥ 48px; primary action reachable without scrolling.
