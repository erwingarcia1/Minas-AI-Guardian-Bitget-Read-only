// The portal's primitives, for a phone (docs/portal-design.md): flat dark
// surfaces, one gold primary per screen, status as a dot AND a word (never
// colour alone), key/value rows instead of tables.
import type { ReactNode } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, radius, space, tone as tones, type, type Tone } from '@/theme';

export function Screen({
  children,
  refreshing,
  onRefresh,
}: {
  children: ReactNode;
  refreshing?: boolean;
  onRefresh?: () => void;
}) {
  return (
    <SafeAreaView style={styles.screen} edges={['left', 'right']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          onRefresh ? (
            <RefreshControl refreshing={!!refreshing} onRefresh={onRefresh} tintColor={colors.textMuted} />
          ) : undefined
        }>
        {children}
      </ScrollView>
    </SafeAreaView>
  );
}

export function Title({ children }: { children: ReactNode }) {
  return <Text style={styles.title}>{children}</Text>;
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <Text style={styles.eyebrow}>{children}</Text>;
}

export function Body({ children, muted, style }: { children: ReactNode; muted?: boolean; style?: StyleProp<TextStyle> }) {
  return <Text style={[styles.body, muted && styles.muted, style]}>{children}</Text>;
}

export function Mono({ children }: { children: ReactNode }) {
  return <Text style={styles.mono}>{children}</Text>;
}

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle} accessibilityRole="header">
        {title}
      </Text>
      {children}
    </View>
  );
}

/** Status: a dot and a word, so it survives colour-blindness and a dim screen. */
export function Badge({ tone = 'neutral', label }: { tone?: Tone; label: string }) {
  const t = tones[tone];
  return (
    <View style={[styles.badge, { backgroundColor: t.bg, borderColor: t.line }]}>
      <View style={[styles.dot, { backgroundColor: t.fg }]} />
      <Text style={[styles.badgeText, { color: t.fg }]}>{label}</Text>
    </View>
  );
}

/** One fact: a small label over its value - the portal's .kv row. */
export function KV({ label, children }: { label: string; children: ReactNode }) {
  return (
    <View style={styles.kv}>
      <Text style={styles.kvLabel}>{label}</Text>
      {typeof children === 'string' || typeof children === 'number' ? (
        <Text style={styles.body}>{children}</Text>
      ) : (
        children
      )}
    </View>
  );
}

export function Button({
  label,
  onPress,
  variant = 'primary',
  busy,
  disabled,
}: {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'danger';
  busy?: boolean;
  disabled?: boolean;
}) {
  const inactive = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!inactive, busy: !!busy }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        variant === 'primary' && { backgroundColor: pressed ? colors.goldPress : colors.gold },
        variant === 'secondary' && styles.buttonSecondary,
        variant === 'danger' && styles.buttonDanger,
        inactive && styles.buttonInactive,
      ]}>
      {busy ? (
        <ActivityIndicator color={variant === 'primary' ? colors.textOnGold : colors.text} />
      ) : (
        <Text
          style={[
            styles.buttonText,
            variant === 'primary' && { color: colors.textOnGold },
            variant === 'danger' && { color: colors.danger },
          ]}>
          {label}
        </Text>
      )}
    </Pressable>
  );
}

export function Notice({ tone = 'neutral', title, children }: { tone?: Tone; title: string; children?: ReactNode }) {
  const t = tones[tone];
  return (
    <View style={[styles.notice, { backgroundColor: t.bg, borderColor: t.line }]} accessibilityRole="alert">
      <Text style={[styles.noticeTitle, { color: tone === 'neutral' ? colors.text : t.fg }]}>{title}</Text>
      {children ? <Text style={styles.body}>{children}</Text> : null}
    </View>
  );
}

/** Loading, failed, or the content - so no screen renders a half-state. */
export function Loaded<T>({
  data,
  error,
  loading,
  children,
}: {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  children: (data: T) => ReactNode;
}) {
  if (data !== undefined) {
    return (
      <>
        {error ? <Notice tone="warn" title="Showing what we had last.">{error}</Notice> : null}
        {children(data)}
      </>
    );
  }
  if (error) return <Notice tone="danger" title="Could not load this.">{error}</Notice>;
  return loading ? <ActivityIndicator color={colors.textMuted} style={{ marginTop: space(6) }} /> : null;
}

/** A timestamp the way a person reads it, in their own time zone. */
export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space(4), gap: space(4), paddingBottom: space(7) },
  title: { color: colors.text, fontSize: type.xxl, fontWeight: '700', lineHeight: type.xxl * 1.2 },
  eyebrow: {
    color: colors.textSubtle,
    fontSize: type.xs,
    fontWeight: '600',
    letterSpacing: 1,
    textTransform: 'uppercase',
  },
  body: { color: colors.text, fontSize: type.base, lineHeight: type.base * 1.55 },
  muted: { color: colors.textMuted },
  mono: { color: colors.text, fontSize: type.sm, fontFamily: 'monospace' },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.md,
    padding: space(4),
    gap: space(3),
  },
  section: { gap: space(3) },
  sectionTitle: { color: colors.text, fontSize: type.lg, fontWeight: '600' },
  badge: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: space(2),
    borderWidth: 1,
    borderRadius: 999,
    paddingVertical: 2,
    paddingHorizontal: space(2),
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  badgeText: { fontSize: type.sm, fontWeight: '600' },
  kv: { gap: 2 },
  kvLabel: { color: colors.textSubtle, fontSize: type.xs, letterSpacing: 0.5, textTransform: 'uppercase' },
  button: {
    minHeight: 48,
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: space(4),
  },
  buttonSecondary: { borderWidth: 1, borderColor: colors.borderStrong, backgroundColor: colors.surface },
  buttonDanger: { borderWidth: 1, borderColor: colors.dangerLine, backgroundColor: colors.dangerBg },
  buttonInactive: { opacity: 0.6 },
  buttonText: { color: colors.text, fontSize: type.base, fontWeight: '600' },
  notice: { borderWidth: 1, borderRadius: radius.md, padding: space(4), gap: space(1) },
  noticeTitle: { fontSize: type.base, fontWeight: '600' },
});
