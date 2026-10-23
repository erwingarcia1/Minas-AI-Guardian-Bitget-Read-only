import { Link } from 'expo-router';
import { useCallback, useRef } from 'react';
import { Pressable } from 'react-native';

import { api, type Schemas } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { severityTone } from '@/theme';
import { Badge, Body, Button, Card, Eyebrow, Loaded, Screen, when } from '@/ui';

const PAGE = 50;
const MOST = 200; // the API's largest page

type Alert = Schemas['AlertSummary'];
type List = { alerts: Alert[]; total: number };

export default function Alerts() {
  // How many rows the member has asked to see: a page, and a page more for
  // each "Show older alerts". Every load - the first, the one on coming back,
  // pull-to-refresh, an older page - reads that many, fresh, from the top. So
  // an acknowledgement made on an incident shows on the way back on any row,
  // new alerts arrive at the top, and no alert can fall between two reads.
  const wanted = useRef(PAGE);
  const load = useCallback(async () => {
    const alerts: Alert[] = [];
    const seen = new Set<number>();
    let total = 0;
    // In reads of at most 200, the API's largest page.
    for (let offset = 0; offset < wanted.current; ) {
      const limit = Math.min(MOST, wanted.current - offset);
      const query = offset === 0 ? { limit } : { limit, offset };
      const { data: page, error, response } = await api.GET('/api/v1/alerts', { params: { query } });
      // A list is only ever shown whole: one failed read keeps the last one.
      if (!page) return { data: undefined, error, response };
      for (const alert of page.alerts) {
        if (!seen.has(alert.id)) {
          seen.add(alert.id);
          alerts.push(alert);
        }
      }
      total = page.total;
      if (page.alerts.length < limit) break; // the end
      offset += page.alerts.length;
    }
    return { data: { alerts, total } satisfies List };
  }, []);
  const { data, error, loading, refreshing, reload, refetch } = useRequest(load);

  function showOlder(list: List) {
    wanted.current = Math.max(wanted.current, list.alerts.length) + PAGE;
    refetch();
  }

  return (
    <Screen refreshing={refreshing} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(list) => (
          <>
            <Eyebrow>{`${list.total} alert(s) on your accounts, newest first`}</Eyebrow>
            {list.alerts.length === 0 ? <Body muted>No alerts. Nothing has been detected on your accounts.</Body> : null}
            {list.alerts.map((alert) => (
              <AlertRow key={alert.id} alert={alert} />
            ))}
            {list.alerts.length < list.total ? (
              <Button label="Show older alerts" variant="secondary" busy={loading} onPress={() => showOlder(list)} />
            ) : null}
          </>
        )}
      </Loaded>
    </Screen>
  );
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
