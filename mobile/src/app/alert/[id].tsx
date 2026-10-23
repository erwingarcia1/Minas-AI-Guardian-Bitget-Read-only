import { Stack, useLocalSearchParams } from 'expo-router';
import { type ReactNode, useCallback, useState } from 'react';
import { View } from 'react-native';

import { api, describeError, type Schemas } from '@/api/client';
import { isTimeout } from '@/api/network';
import { useRequest } from '@/api/use-request';
import { severityTone, space } from '@/theme';
import { Badge, Body, Button, Card, KV, Loaded, Mono, Notice, Screen, Section, Title, when } from '@/ui';

type Incident = Schemas['Incident'];

// One incident, from the record the portal's incident page renders
// (incident_record): the same evidence, timeline, balances and the same
// what-to-do-next steps the alert email carried.
export default function IncidentScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const alertId = Number(id);
  const load = useCallback(() => api.GET('/api/v1/alerts/{id}', { params: { path: { id: alertId } } }), [alertId]);
  const { data, error, loading, refreshing, reload, refetch, setData } = useRequest(load);

  // The acknowledgement shows the moment the server takes it - the answer
  // says who and when - and the record is then re-read in the background, so
  // a failed re-read cannot bring the button back.
  const acknowledged = useCallback(
    (result: Schemas['Acknowledgement']) => {
      setData((incident) =>
        incident && { ...incident, acknowledged_at: result.acknowledged_at, acknowledged_by: result.acknowledged_by },
      );
      refetch();
    },
    [setData, refetch],
  );

  return (
    <Screen refreshing={refreshing} onRefresh={reload}>
      <Stack.Screen options={{ title: Number.isInteger(alertId) ? `Incident #${alertId}` : 'Incident' }} />
      <Loaded data={data} error={error} loading={loading}>
        {(incident) => <Record incident={incident} onAcknowledged={acknowledged} />}
      </Loaded>
    </Screen>
  );
}

function Record({
  incident,
  onAcknowledged,
}: {
  incident: Incident;
  onAcknowledged: (result: Schemas['Acknowledgement']) => void;
}) {
  const reachedNobody = incident.delivered_to === 0;
  return (
    <>
      <Badge tone={severityTone[incident.severity] ?? 'neutral'} label={incident.severity} />
      <Title>{incident.message}</Title>
      <KV label="Detected">{when(incident.sent_at)}</KV>
      <KV label="Account">{`${capitalise(incident.exchange)} — ${incident.account_name}`}</KV>
      <KV label="Incident">{`#${incident.id} · ${incident.rule} · confidence ${incident.confidence ?? '—'}`}</KV>
      {incident.repeat_count ? (
        <KV label="Still firing">
          {`Repeated ${incident.repeat_count}× since${incident.last_repeat_at ? `, last ${when(incident.last_repeat_at)}` : ''}`}
        </KV>
      ) : null}

      {/* What to do comes first, as on the portal: read, then act. */}
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
        Guardian is read-only. It can&apos;t revoke keys, cancel withdrawals, or freeze accounts. Only you and the
        exchange can.
      </Notice>

      <Acknowledge incident={incident} onAcknowledged={onAcknowledged} />

      <Section title="Timeline">
        <Card>
          <Step done label="Detected" when={incident.sent_at}>
            {incident.message}
          </Step>
          <Step done={!reachedNobody} label={reachedNobody ? 'Not delivered' : 'Notified'} when={incident.sent_at}>
            {reachedNobody
              ? 'This alert was detected and recorded but reached nobody. No notification was successfully sent.'
              : `Sent to ${incident.delivered_to} channel(s)`}
          </Step>
          <Step
            done={!!incident.acknowledged_at}
            label={incident.acknowledged_at ? 'Acknowledged' : 'Not acknowledged'}
            when={incident.acknowledged_at}>
            {incident.acknowledged_at
              ? `By ${incident.acknowledged_by ?? 'unknown'} at ${when(incident.acknowledged_at)}`
              : 'Nobody has confirmed seeing this alert.'}
          </Step>
          {incident.expected_marks.map((mark) => (
            <Step key={`${mark.marked_by}-${mark.created_at}`} done label="Marked as expected activity" when={mark.created_at}>
              {`By ${mark.marked_by}${mark.note ? ` — “${mark.note}”` : ''}`}
            </Step>
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

/** One timeline entry, marked as the portal marks it: Done, or Attention. */
function Step({ done, label, when: at, children }: { done: boolean; label: string; when: string | null; children: ReactNode }) {
  return (
    <KV label={label}>
      <View style={{ gap: space(1) }}>
        <Badge tone={done ? 'ok' : 'warn'} label={done ? 'Done' : 'Attention'} />
        <Body>{children}</Body>
        {at ? <Body muted>{when(at)}</Body> : null}
      </View>
    </KV>
  );
}

function Acknowledge({
  incident,
  onAcknowledged,
}: {
  incident: Incident;
  onAcknowledged: (result: Schemas['Acknowledgement']) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  if (incident.acknowledged_at) return null;

  async function acknowledge() {
    setBusy(true);
    setFailure(null);
    const { data, error, response, thrown } = await api
      .POST('/api/v1/alerts/{id}/acknowledge', { params: { path: { id: incident.id } } })
      .then((result) => ({ ...result, thrown: undefined }))
      .catch((caught: unknown) => ({ data: undefined, error: undefined, response: undefined, thrown: caught }));
    setBusy(false);
    if (data) onAcknowledged(data);
    else if (isTimeout(thrown)) {
      // Minas may have recorded it before the answer was lost.
      setFailure("Minas didn't answer in time. The acknowledgement may still have been recorded: pull down to check.");
    } else setFailure(describeError(error, response, thrown));
  }

  return (
    <Card>
      {/* The portal's own warning, beside the same button. */}
      <Body>
        <Body style={{ fontWeight: '700' }}>Acknowledge doesn&apos;t stop the attack.</Body> It only marks this alert
        as seen.
      </Body>
      {failure ? (
        <Notice tone="danger" title="Not acknowledged.">
          {failure}
        </Notice>
      ) : null}
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
