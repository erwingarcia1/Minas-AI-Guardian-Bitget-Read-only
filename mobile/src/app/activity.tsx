import { useCallback } from 'react';

import { api } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { Body, Card, KV, Loaded, Screen, Title, when } from '@/ui';

// The portal's activity page: every change to how the accounts are watched or
// reached, including Minas's own. Append-only; nothing here is ever edited,
// except that an erased person's details read [erased] (decision 6b).
//
// A copy of the portal's ACTIVITY_PROMISE, kept equal by tests/test_mobile_app.py.
const PROMISE =
  'This record is append-only: entries are never edited or removed, by us or by anybody else, with one exception: when a person named in it asks us to delete their data, their email address, phone number and Telegram identity are replaced with [erased], and an entry here says so.';

export default function Activity() {
  const load = useCallback(() => api.GET('/api/v1/activity'), []);
  const { data, error, loading, refreshing, reload } = useRequest(load);

  return (
    <Screen refreshing={refreshing} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(activity) => (
          <>
            <Body muted>
              {`${activity.events.length} change(s). Detections are not listed here — they are under Alerts. ` + PROMISE}
            </Body>
            {activity.events.length === 0 ? (
              <Body muted>
                Nothing recorded — no changes to your accounts or your login have been made since monitoring began.
              </Body>
            ) : null}
            {activity.events.map((event) => (
              <Card key={event.id}>
                <Title>{event.title}</Title>
                <Body muted>{`${event.account_name} · ${when(event.created_at)}`}</Body>
                {event.detail ? <KV label="Detail">{event.detail}</KV> : null}
                {/* A change nobody can be named for is a finding, not a blank. */}
                <KV label="Who">{event.actor ?? 'not attributed'}</KV>
              </Card>
            ))}
          </>
        )}
      </Loaded>
    </Screen>
  );
}
