import { Link } from 'expo-router';
import { useCallback } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { api, type Schemas } from '@/api/client';
import { useNow, useRequest } from '@/api/use-request';
import { colors, radius, space, type, tone as tones, type Tone } from '@/theme';
import { Badge, Body, Card, Eyebrow, KV, Loaded, Notice, Screen, Title, when } from '@/ui';

type Overall = { tone: Tone; eyebrow: string; title: string; detail: string };

// The portal's own answer to "am I being watched?" (sentinel/portal.py
// _OVERALL_COPY), word for word: the app and the web say the same thing.
const OVERALL: Record<Schemas['Accounts']['overall'], Overall> = {
  watching: {
    tone: 'ok',
    eyebrow: 'Monitoring normal',
    title: "You're being watched.",
    detail: 'Every connected account was checked within the last few minutes.',
  },
  degraded: {
    tone: 'warn',
    eyebrow: 'Partially watched',
    title: 'Some of your accounts are not being watched.',
    detail: 'Monitoring is running, but not every account is being checked. The ones affected are marked below.',
  },
  not_watching: {
    tone: 'danger',
    eyebrow: 'Not being watched',
    title: 'Your accounts are NOT being watched.',
    detail: 'Movements on them are not currently being detected. Minas has been alerted; what each account needs is marked below.',
  },
};

// A verdict this app version has no words for (a newer server) is never
// rendered as good news.
const UNKNOWN: Overall = {
  tone: 'warn',
  eyebrow: 'Status unknown',
  title: "This app can't tell whether you're being watched.",
  detail: 'Update the app, or check the member portal, which always says.',
};

// The portal home's account-card states (portal.py _watch_state): Paused is a
// choice, not a fault; everything else that is not watched is Trouble.
function watchState(account: Schemas['Account']): { tone: Tone; label: string } {
  if (account.watched) return { tone: 'ok', label: 'Watching' };
  if (account.status === 'disabled') return { tone: 'neutral', label: 'Paused' };
  return { tone: 'danger', label: 'Trouble' };
}

// What the member can do about an account that is not being watched. The
// server says what kind of trouble it is (`cause`); only a rejected key is the
// member's to fix, and only the account's main contact can replace it.
function fix(account: Schemas['Account']): string | null {
  switch (account.cause) {
    case 'key_rejected':
      return account.role === 'primary'
        ? "You can fix this: replace the API key on the member portal, on this account's page."
        : 'The account holder can fix this by replacing the API key on the member portal.';
    case 'never_checked':
    case 'no_recent_check':
    case 'not_active':
      return 'If this lasts, contact the Minas team.';
    default:
      return null;
  }
}

export default function Accounts() {
  const load = useCallback(() => api.GET('/api/v1/accounts'), []);
  // Re-asked every 30 seconds while this tab is in view: it is the one
  // screen that must never show an old "yes".
  const { data, error, loading, refreshing, receivedAt, reload } = useRequest(load, { every: 30_000 });
  const now = useNow(15_000);

  return (
    <Screen refreshing={refreshing} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(accounts) => {
          if (accounts.accounts.length === 0) {
            return <Body muted>No accounts yet — contact the Minas team.</Body>;
          }
          // The verdict holds for the monitoring window from when it reached
          // this phone (the phone's clock against itself, so a phone set to
          // the wrong time cannot make an old answer look new).
          const expired = receivedAt !== null && now - receivedAt > accounts.stale_after_seconds * 1000;
          const overall = OVERALL[accounts.overall] ?? UNKNOWN;
          return (
            <>
              {accounts.attention ? <Attention alert={accounts.attention} /> : null}
              {accounts.pending_pauses.length > 0 ? (
                <Notice tone="warn" title="Monitoring is about to stop.">
                  {`${accounts.pending_pauses
                    .map((pause) => `${pause.account_name} at ${when(pause.execute_at)}`)
                    .join('; ')}. Cancel it on the member portal, on the account's page — and if you did not ` +
                    'ask for this, contact Minas directly. We never email links.'}
                </Notice>
              ) : null}
              {expired ? (
                <Notice title="Checking again…">
                  {`This answer is from ${when(accounts.checked_at)}, too old to say whether you're being watched now. Pull down to check.`}
                </Notice>
              ) : (
                <Notice tone={overall.tone} title={overall.title}>
                  {overall.detail}
                </Notice>
              )}
              <Eyebrow>{`${expired ? 'Last answer' : overall.eyebrow} · checked ${when(accounts.checked_at)}`}</Eyebrow>
              {accounts.accounts.map((account) => {
                const state = watchState(account);
                return (
                  <Card key={account.id}>
                    <Title>{account.name}</Title>
                    <Badge tone={state.tone} label={state.label} />
                    {state.label === 'Trouble' && account.reason ? (
                      <KV label="What's wrong">{account.reason}</KV>
                    ) : null}
                    {state.label === 'Trouble' && fix(account) ? <Body muted>{fix(account)}</Body> : null}
                    <KV label="Exchange">{(account.exchange ?? 'unknown').replace(/^./, (c) => c.toUpperCase())}</KV>
                    <KV label="Last successful check">
                      {account.last_success_at ? when(account.last_success_at) : 'Never'}
                    </KV>
                    <KV label="Key permissions">Read-only</KV>
                    {account.role === 'non-primary' ? (
                      <Body muted>You are the emergency contact on this account: you can read and acknowledge.</Body>
                    ) : null}
                  </Card>
                );
              })}
            </>
          );
        }}
      </Loaded>
    </Screen>
  );
}

/** The home page's first block (decision 16): the newest incident nobody has
 * acknowledged, one tap from its record. */
function Attention({ alert }: { alert: Schemas['Attention'] }) {
  // The portal's rule: CRITICAL and HIGH are red, the rest amber.
  const t = tones[alert.severity === 'CRITICAL' || alert.severity === 'HIGH' ? 'danger' : 'warn'];
  const title = `Needs your attention — incident #${alert.id}`;
  const detail = `${alert.severity}: ${alert.message} on ${alert.account_name}, ${when(alert.sent_at)} — not yet acknowledged.`;
  return (
    <Link href={{ pathname: '/alert/[id]', params: { id: String(alert.id) } }} asChild>
      <Pressable accessibilityRole="link" accessibilityLabel={`${title}. ${detail} Open the incident.`}>
        <View style={[styles.attention, { backgroundColor: t.bg, borderColor: t.line }]}>
          <Text style={[styles.attentionTitle, { color: t.fg }]}>{title}</Text>
          <Text style={styles.attentionText}>{detail}</Text>
          <Text style={styles.attentionLink}>Open the incident and acknowledge it ›</Text>
        </View>
      </Pressable>
    </Link>
  );
}

const styles = StyleSheet.create({
  attention: { borderWidth: 1, borderRadius: radius.md, padding: space(4), gap: space(1) },
  attentionTitle: { fontSize: type.base, fontWeight: '600' },
  attentionText: { color: colors.text, fontSize: type.base, lineHeight: type.base * 1.55 },
  attentionLink: { color: colors.gold, fontSize: type.base, fontWeight: '600' },
});
