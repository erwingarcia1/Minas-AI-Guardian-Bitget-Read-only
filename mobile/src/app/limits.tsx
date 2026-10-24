import { useCallback } from 'react';

import { api } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { Badge, Body, Card, KV, Loaded, Notice, Screen, Section, Title } from '@/ui';

// The limits page, from the same sentences and the running configuration
// (G-44, #48): unflattering on purpose. A customer who believes the Guardian
// can stop an attack will not act on an alert with the urgency it needs.
export default function Limits() {
  const load = useCallback(() => api.GET('/api/v1/limits'), []);
  const { data, error, loading, refreshing, reload } = useRequest(load);

  return (
    <Screen refreshing={refreshing} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(limits) => (
          <>
            <Body muted>{limits.lead}</Body>
            {/* Standing text, the portal's callout (role="note"): not announced. */}
            <Notice tone="danger" title={limits.headline.title} live={false}>
              {limits.headline.text}
            </Notice>

            <Section title="What the Guardian does">
              {limits.does.map((item) => (
                <Card key={item.title}>
                  <Title>{item.title}</Title>
                  <Body muted>{item.text}</Body>
                </Card>
              ))}
            </Section>

            <Section title="What it cannot do">
              {limits.cannot.map((item) => (
                <Card key={item.title}>
                  <Title>{item.title}</Title>
                  <Body muted>{item.text}</Body>
                  {item.see_status ? <Body muted>The Accounts tab says whether you are being watched right now.</Body> : null}
                </Card>
              ))}
            </Section>

            <Section title={limits.why.title}>
              <Body>{limits.why.text}</Body>
            </Section>

            <Section title="Honest timing">
              <Body>
                {`Your balances and recent movements are checked about every ${limits.timing.balances_every_seconds} ` +
                  `seconds; permissions and allowlists about every ${Math.round(limits.timing.permissions_every_seconds / 60)} ` +
                  'minutes. An alert is sent as soon as a check finds something, so in normal conditions you hear ' +
                  'within a minute or so of the exchange showing it. That is measured from when the exchange reports ' +
                  'it, not from when it happened — we cannot be faster than the source we read.'}
              </Body>
            </Section>

            <Section title="What we keep, and for how long">
              <Card>
                {limits.retention.map((row) => (
                  <KV key={row.what} label={row.what}>
                    {row.how_long}
                  </KV>
                ))}
              </Card>
              {limits.retention_reviewed ? null : (
                <Notice title="These periods are our current practice, not yet reviewed policy.">
                  A legal review of retention obligations is still pending. If it changes these numbers, this screen
                  changes with them.
                </Notice>
              )}
            </Section>

            <Section title="Your alert sensitivity">
              {limits.sensitivity.map((account) => (
                <Card key={account.account_id}>
                  <Title>{account.account_name}</Title>
                  {account.thresholds.map((row) => (
                    <KV key={row.setting} label={row.title}>
                      <Body>{row.phrase}</Body>
                      {row.set_for_this_account ? <Badge label="Set for this account" /> : null}
                    </KV>
                  ))}
                </Card>
              ))}
            </Section>
          </>
        )}
      </Loaded>
    </Screen>
  );
}
