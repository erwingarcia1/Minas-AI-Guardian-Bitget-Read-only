import { Link } from 'expo-router';
import { useCallback, useState } from 'react';
import { Alert as Confirm } from 'react-native';

import { api, describeError } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { useSession } from '@/auth/session';
import { Badge, Body, Button, Card, KV, Loaded, Notice, Screen, Section, Title, when } from '@/ui';

export default function Security() {
  const { signOut } = useSession();
  const load = useCallback(() => api.GET('/api/v1/security'), []);
  const { data, error, loading, reload } = useRequest(load);
  const [failure, setFailure] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);

  async function revoke(id: number) {
    setFailure(null);
    const { error: problem, response } = await api
      .DELETE('/api/v1/devices/{id}', { params: { path: { id } } })
      .catch(() => ({ error: undefined, response: undefined }));
    if (response?.status === 204) reload();
    else setFailure(describeError(problem, response));
  }

  function confirmSignOut() {
    Confirm.alert('Sign out of this phone?', 'You will stop seeing alerts here until you sign in again.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign out',
        style: 'destructive',
        onPress: async () => {
          setLeaving(true);
          await signOut();
        },
      },
    ]);
  }

  return (
    <Screen refreshing={loading} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(security) => (
          <>
            <Section title="Two-factor sign-in">
              <Card>
                <Badge tone="ok" label="On" />
                <KV label="Recovery codes left">{security.recovery_codes_left}</KV>
                <Body muted>Turning it off, and new recovery codes, are on the portal's Security page.</Body>
              </Card>
            </Section>

            <Section title="Phones signed in to the app">
              <Body muted>
                Each of these can read your alerts and acknowledge them. If you do not recognise one, sign it out
                and change your password.
              </Body>
              {failure ? <Notice tone="danger" title="Not signed out.">{failure}</Notice> : null}
              {security.devices.map((device) => (
                <Card key={device.id}>
                  <Title>{device.name}</Title>
                  {device.current ? <Badge tone="ok" label="This phone" /> : null}
                  <KV label="Signed in">{when(device.signed_in_at)}</KV>
                  <KV label="Last active">{when(device.last_refreshed_at ?? device.signed_in_at)}</KV>
                  {device.current ? null : (
                    <Button label="Sign out this phone" variant="secondary" onPress={() => revoke(device.id)} />
                  )}
                </Card>
              ))}
            </Section>
          </>
        )}
      </Loaded>

      <Section title="More">
        <Link href="/activity" asChild>
          <Button label="Who has touched your accounts" variant="secondary" onPress={() => {}} />
        </Link>
        <Link href="/limits" asChild>
          <Button label="What the Guardian can and cannot do" variant="secondary" onPress={() => {}} />
        </Link>
        <Body muted>Alert channels, quiet hours, Telegram and your data are on the member portal.</Body>
      </Section>

      <Button label="Sign out of this phone" variant="danger" busy={leaving} onPress={confirmSignOut} />
    </Screen>
  );
}
