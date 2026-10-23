// The portal's identity, on a phone (decision 21g). tokens.json holds the
// portal's own :root values; tests/test_mobile_app.py (in the server's suite)
// fails if any of them stops matching sentinel/portal.py, so the app and the
// portal cannot drift into two spellings of gold.
import tokens from './tokens.json';

export const colors = {
  bg: tokens.bg,
  surface: tokens.surface,
  surface2: tokens['surface-2'],
  border: tokens.border,
  borderStrong: tokens['border-strong'],
  text: tokens.text,
  textMuted: tokens['text-muted'],
  textSubtle: tokens['text-subtle'],
  textOnGold: tokens['text-on-gold'],
  gold: tokens.gold,
  goldPress: tokens['gold-press'],
  violet: tokens.violet,
  violetText: tokens['violet-text'],
  danger: tokens.danger,
  dangerBg: tokens['danger-bg'],
  dangerLine: tokens['danger-line'],
  warn: tokens.warn,
  warnBg: tokens['warn-bg'],
  warnLine: tokens['warn-line'],
  ok: tokens.ok,
  okBg: tokens['ok-bg'],
  okLine: tokens['ok-line'],
} as const;

export const radius = { md: parseInt(tokens.radius, 10), sm: parseInt(tokens['radius-sm'], 10) };

// The portal's 4px spacing base and its type scale at a 16px root (body is
// 16 minimum: read on a phone at 3am, not a marketing page).
export const space = (n: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8) => [0, 4, 8, 12, 16, 24, 32, 48, 64][n];
export const type = { xs: 12, sm: 14, base: 16, lg: 18, xl: 22, xxl: 28 } as const;

export type Tone = 'ok' | 'warn' | 'danger' | 'neutral';

export const tone = {
  ok: { fg: colors.ok, bg: colors.okBg, line: colors.okLine },
  warn: { fg: colors.warn, bg: colors.warnBg, line: colors.warnLine },
  danger: { fg: colors.danger, bg: colors.dangerBg, line: colors.dangerLine },
  neutral: { fg: colors.textMuted, bg: colors.surface2, line: colors.border },
} as const;

// The portal's mappings (sentinel/portal.py _SEV_BADGE, _STATUS_BADGE):
// CRITICAL is the red that means act now; HIGH warns; the rest stay neutral so
// the loud tones keep their meaning.
export const severityTone: Record<string, Tone> = {
  CRITICAL: 'danger',
  HIGH: 'warn',
  MEDIUM: 'neutral',
  LOW: 'neutral',
};
