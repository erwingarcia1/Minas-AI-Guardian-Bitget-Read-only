import { useCallback } from 'react';

import { api, type Schemas } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { type Tone } from '@/theme';
import { Badge, Body, Card, Eyebrow, KV, Loaded, Notice, Screen, Title, when } from '@/ui';

// The portal's own answer to "am I being watched?" (sentinel/portal.py
// _OVERALL_COPY), word for word: the app and the web say the same thing.
const OVERALL: Record<Schemas['Accounts']['overall'], { tone: Tone; eyebrow: string; title: string; detail: string }> = {
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

export default function Accounts() {
  const load = useCallback(() => api.GET('/api/v1/accounts'), []);
  const { data, error, loading, reload } = useRequest(load);

  return (
    <Screen refreshing={loading} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(accounts) => {
          const overall = OVERALL[accounts.overall];
          return (
            <>
              <Notice tone={overall.tone} title={overall.title}>
                {overall.detail}
              </Notice>
              <Eyebrow>{`${overall.eyebrow} · checked ${when(accounts.checked_at)}`}</Eyebrow>
              {accounts.accounts.length === 0 ? (
                <Body muted>No accounts are connected to this login.</Body>
              ) : null}
              {accounts.accounts.map((account) => (
                <Card key={account.id}>
                  <Title>{account.name}</Title>
                  <Badge
                    tone={account.watched ? 'ok' : account.status === 'disabled' ? 'neutral' : 'danger'}
                    label={account.watched ? 'Being watched' : 'Not being watched'}
                  />
                  {account.reason ? <Body>{account.reason}</Body> : null}
                  <KV label="Exchange">{(account.exchange ?? 'unknown').replace(/^./, (c) => c.toUpperCase())}</KV>
                  <KV label="Last successful check">{when(account.last_success_at)}</KV>
                  {account.role === 'non-primary' ? (
                    <Body muted>You are the emergency contact on this account: you can read and acknowledge.</Body>
                  ) : null}
                </Card>
              ))}
            </>
          );
        }}
      </Loaded>
    </Screen>
  );
}
