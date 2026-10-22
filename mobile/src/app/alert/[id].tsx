import { useLocalSearchParams } from 'expo-router';
import { useCallback, useState } from 'react';

import { api, describeError, type Schemas } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { severityTone } from '@/theme';
import { Badge, Body, Button, Card, KV, Loaded, Mono, Notice, Screen, Section, Title, when } from '@/ui';

// One incident, from the record the portal's incident page renders
// (incident_record): the same evidence, timeline, balances and the same
// what-to-do-next steps the alert email carried.
export default function Incident() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const alertId = Number(id);
  const load = useCallback(() => api.GET('/api/v1/alerts/{id}', { params: { path: { id: alertId } } }), [alertId]);
  const { data, error, loading, reload } = useRequest(load);

  return (
    <Screen refreshing={loading} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(incident) => <Record incident={incident} onAcknowledged={reload} />}
      </Loaded>
    </Screen>
  );
}

function Record({ incident, onAcknowledged }: { incident: Schemas['Incident']; onAcknowledged: () => void }) {
  return (
    <>
      <Badge tone={severityTone[incident.severity] ?? 'neutral'} label={incident.severity} />
      <Title>{incident.message}</Title>
      <KV label="Detected">{when(incident.sent_at)}</KV>
      <KV label="Account">{`${capitalise(incident.exchange)} — ${incident.account_name}`}</KV>
      <KV label="Incident">{`#${incident.id} · ${incident.rule} · confidence ${incident.confidence ?? '—'}`}</KV>

      <Acknowledge incident={incident} onAcknowledged={onAcknowledged} />

      {incident.advice ? (
        <Section title="What to do next">
          <Card>
            {incident.advice.lead ? <Body>{incident.advice.lead}</Body> : null}
            {incident.advice.steps.map((step, i) => (
              <Body key={step}>{`${i + 1}. ${step}`}</Body>
            ))}
            {incident.advice.note ? <Body muted>{incident.advice.note}</Body> : null}
          </Card>
        </Section>
      ) : null}

      <Notice title="Minas cannot do this for you.">
        Guardian is read-only. It can't revoke keys, cancel withdrawals, or freeze accounts. Only you and the
        exchange can.
      </Notice>

      <Section title="Timeline">
        <Card>
          <KV label="Detected">{when(incident.sent_at)}</KV>
          <KV label="Notified">
            {incident.delivered_to > 0
              ? `Sent to ${incident.delivered_to} channel(s)`
              : 'Not delivered — this alert was detected and recorded but reached nobody.'}
          </KV>
          <KV label="Acknowledged">
            {incident.acknowledged_at
              ? `By ${incident.acknowledged_by ?? 'unknown'} at ${when(incident.acknowledged_at)}`
              : 'Not acknowledged — nobody has confirmed seeing this alert.'}
          </KV>
          {incident.expected_marks.map((mark) => (
            <KV key={`${mark.marked_by}-${mark.created_at}`} label="Marked as expected activity">
              {`By ${mark.marked_by} at ${when(mark.created_at)}${mark.note ? ` — “${mark.note}”` : ''}`}
            </KV>
          ))}
        </Card>
      </Section>

      <Section title="What was detected">
        <Card>
          {Object.keys(incident.evidence).length === 0 ? <Body muted>No structured evidence recorded.</Body> : null}
          {Object.entries(incident.evidence)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([key, value]) => (
              <KV key={key} label={key}>
                <Mono>{typeof value === 'string' ? value : JSON.stringify(value)}</Mono>
              </KV>
            ))}
        </Card>
      </Section>

      <Balances title="Balances before" when="before" rows={incident.balances_before} />
      <Balances title="Balances after" when="after" rows={incident.balances_after} />
    </>
  );
}

function Acknowledge({ incident, onAcknowledged }: { incident: Schemas['Incident']; onAcknowledged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  if (incident.acknowledged_at) return null;

  async function acknowledge() {
    setBusy(true);
    setFailure(null);
    const { data, error, response } = await api
      .POST('/api/v1/alerts/{id}/acknowledge', { params: { path: { id: incident.id } } })
      .catch(() => ({ data: undefined, error: undefined, response: undefined }));
    setBusy(false);
    if (data) onAcknowledged();
    else setFailure(describeError(error, response));
  }

  return (
    <Card>
      {/* The portal's own warning, beside the same button. */}
      <Body>
        <Body style={{ fontWeight: '700' }}>Acknowledge doesn't stop the attack.</Body> It only marks this alert as
        seen.
      </Body>
      {failure ? <Notice tone="danger" title="Not acknowledged.">{failure}</Notice> : null}
      <Button label="Acknowledge alert" onPress={acknowledge} busy={busy} />
    </Card>
  );
}

function Balances({ title, when: side, rows }: { title: string; when: string; rows: Schemas['Balance'][] }) {
  return (
    <Section title={title}>
      <Card>
        {rows.length === 0 ? <Body muted>{`No snapshot ${side} this alert.`}</Body> : null}
        {rows.map((row) => (
          <KV key={row.coin} label={row.coin}>
            {/* Exact, as stored: a quantity is never parsed into a float. */}
            <Mono>{`${row.quantity}  ·  ${when(row.snapshot_at)}`}</Mono>
          </KV>
        ))}
      </Card>
    </Section>
  );
}

function capitalise(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
