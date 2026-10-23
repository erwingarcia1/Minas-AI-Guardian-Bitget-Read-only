import { Link } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable } from 'react-native';

import { api, describeError, type Schemas } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { severityTone } from '@/theme';
import { Badge, Body, Button, Card, Eyebrow, Loaded, Notice, Screen, when } from '@/ui';

const PAGE = 50;
const MOST = 200; // the API's largest page

type Alert = Schemas['AlertSummary'];
/** What is on screen: the rows, the server's count, where the next older page
 * starts, and whether newer alerts have arrived since the list was loaded. */
type List = { alerts: Alert[]; total: number; next: number; newer: boolean };

export default function Alerts() {
  // A reload fetches everything already on screen in one call, fresh - so an
  // acknowledgement made on the incident shows on the way back, on any page,
  // and a refresh can never leave a gap between the top and the older pages.
  const shown = useRef(PAGE);
  const load = useCallback(async () => {
    const limit = Math.min(MOST, Math.max(PAGE, shown.current));
    const { data: page, error, response } = await api.GET('/api/v1/alerts', { params: { query: { limit } } });
    const list: List | undefined = page && { alerts: page.alerts, total: page.total, next: page.alerts.length, newer: false };
    return { data: list, error, response };
  }, []);
  const { data, error, loading, refreshing, reload, setData } = useRequest(load);
  useEffect(() => {
    if (data) shown.current = data.alerts.length;
  }, [data]);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadMore(base: List) {
    setBusy(true);
    setMoreError(null);
    const { data: page, error: failure, response, thrown } = await api
      .GET('/api/v1/alerts', { params: { query: { limit: PAGE, offset: base.next } } })
      .then((result) => ({ ...result, thrown: undefined }))
      .catch((caught: unknown) => ({ data: undefined, error: undefined, response: undefined, thrown: caught }));
    setBusy(false);
    if (!page) {
      setMoreError(describeError(failure, response, thrown));
      return;
    }
    // Dropped if the list was reloaded meanwhile: the reload already holds
    // these rows, fresher.
    setData((list) => (list === base ? append(list, page) : list));
  }

  return (
    <Screen refreshing={refreshing} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(list) => (
          <>
            <Eyebrow>{`${list.total} alert(s) on your accounts, newest first`}</Eyebrow>
            {list.newer ? (
              <Notice tone="warn" title="New alerts have arrived.">
                Pull down to see them at the top.
              </Notice>
            ) : null}
            {list.alerts.length === 0 ? <Body muted>No alerts. Nothing has been detected on your accounts.</Body> : null}
            {list.alerts.map((alert) => (
              <AlertRow key={alert.id} alert={alert} />
            ))}
            {moreError ? (
              <Notice tone="danger" title="Could not load more.">
                {moreError}
              </Notice>
            ) : null}
            {list.next < list.total ? (
              <Button
                label="Show older alerts"
                variant="secondary"
                busy={busy}
                disabled={loading}
                onPress={() => loadMore(list)}
              />
            ) : null}
          </>
        )}
      </Loaded>
    </Screen>
  );
}

/** An older page added to the list. Pages are by offset, so alerts arriving in
 * between shift what a page holds: rows already shown are dropped (each alert
 * once, whatever moved), and the next page starts where this one ended. */
function append(list: List, page: Schemas['AlertList']): List {
  const have = new Set(list.alerts.map((alert) => alert.id));
  return {
    alerts: [...list.alerts, ...page.alerts.filter((alert) => !have.has(alert.id))],
    total: page.total,
    // An empty page means the end, whatever the count said.
    next: page.alerts.length === 0 ? page.total : list.next + page.alerts.length,
    newer: list.newer || page.total > list.total,
  };
}

function AlertRow({ alert }: { alert: Alert }) {
  const repeat = alert.repeat_count
    ? `Still firing — repeated ${alert.repeat_count}× since${alert.last_repeat_at ? `, last ${when(alert.last_repeat_at)}` : ''}`
    : null;
  const acknowledged = alert.acknowledged_at ? `Acknowledged by ${alert.acknowledged_by ?? 'someone'}` : 'Not acknowledged';
  // Everything the row shows, in the order it shows it, for a screen reader.
  const spoken = [
    `${alert.severity} alert`,
    alert.message,
    `Incident ${alert.id}`,
    alert.account_name,
    when(alert.sent_at),
    repeat,
    acknowledged,
    alert.delivered_to === 0 ? 'Reached nobody when it fired' : null,
  ]
    .filter(Boolean)
    .join('. ');

  return (
    <Link href={{ pathname: '/alert/[id]', params: { id: String(alert.id) } }} asChild>
      <Pressable accessibilityRole="link" accessibilityLabel={spoken}>
        <Card>
          <Badge tone={severityTone[alert.severity] ?? 'neutral'} label={alert.severity} />
          <Body>{alert.message}</Body>
          <Body muted>{`#${alert.id} · ${alert.account_name} · ${when(alert.sent_at)}`}</Body>
          {repeat ? <Body muted>{repeat}</Body> : null}
          <Badge tone={alert.acknowledged_at ? 'ok' : 'warn'} label={acknowledged} />
          {alert.delivered_to === 0 ? <Badge tone="warn" label="Reached nobody when it fired" /> : null}
        </Card>
      </Pressable>
    </Link>
  );
}
