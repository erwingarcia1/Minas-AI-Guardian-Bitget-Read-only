import { Link } from 'expo-router';
import { useCallback, useState } from 'react';
import { Pressable } from 'react-native';

import { api, describeError, type Schemas } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { severityTone } from '@/theme';
import { Badge, Body, Button, Card, Eyebrow, Loaded, Notice, Screen, when } from '@/ui';

const PAGE = 50;

export default function Alerts() {
  const load = useCallback(() => api.GET('/api/v1/alerts', { params: { query: { limit: PAGE } } }), []);
  const { data, error, loading, reload } = useRequest(load);
  const [more, setMore] = useState<Schemas['AlertSummary'][]>([]);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    setMore([]);
    setMoreError(null);
    reload();
  }, [reload]);

  async function loadMore(offset: number) {
    setBusy(true);
    const { data: page, error: failure, response } = await api
      .GET('/api/v1/alerts', { params: { query: { limit: PAGE, offset } } })
      .catch(() => ({ data: undefined, error: undefined, response: undefined }));
    setBusy(false);
    if (page) setMore((shown) => [...shown, ...page.alerts]);
    else setMoreError(describeError(failure, response));
  }

  return (
    <Screen refreshing={loading} onRefresh={refresh}>
      <Loaded data={data} error={error} loading={loading}>
        {(first) => {
          const alerts = [...first.alerts, ...more];
          return (
            <>
              <Eyebrow>{`${first.total} alert(s) on your accounts, newest first`}</Eyebrow>
              {alerts.length === 0 ? <Body muted>No alerts. Nothing has been detected on your accounts.</Body> : null}
              {alerts.map((alert) => (
                <AlertRow key={alert.id} alert={alert} />
              ))}
              {moreError ? <Notice tone="danger" title="Could not load more.">{moreError}</Notice> : null}
              {alerts.length < first.total ? (
                <Button label="Show older alerts" variant="secondary" busy={busy} onPress={() => loadMore(alerts.length)} />
              ) : null}
            </>
          );
        }}
      </Loaded>
    </Screen>
  );
}

function AlertRow({ alert }: { alert: Schemas['AlertSummary'] }) {
  return (
    <Link href={{ pathname: '/alert/[id]', params: { id: String(alert.id) } }} asChild>
      <Pressable accessibilityRole="link" accessibilityLabel={`${alert.severity} alert: ${alert.message}`}>
        <Card>
          <Badge tone={severityTone[alert.severity] ?? 'neutral'} label={alert.severity} />
          <Body>{alert.message}</Body>
          <Body muted>{`${alert.account_name} · ${when(alert.sent_at)}`}</Body>
          {alert.acknowledged_at ? (
            <Badge tone="ok" label={`Acknowledged by ${alert.acknowledged_by ?? 'someone'}`} />
          ) : (
            <Badge tone="warn" label="Not acknowledged" />
          )}
          {alert.delivered_to === 0 ? <Body muted>This alert reached nobody when it fired.</Body> : null}
        </Card>
      </Pressable>
    </Link>
  );
}
