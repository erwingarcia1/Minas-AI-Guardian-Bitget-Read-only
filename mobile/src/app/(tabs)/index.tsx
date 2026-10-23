import { useCallback } from 'react';

import { api, type Schemas } from '@/api/client';
import { useNow, useRequest } from '@/api/use-request';
import { type Tone } from '@/theme';
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
