import { Link } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert as Confirm, Linking } from 'react-native';

import { api, describeError } from '@/api/client';
import { useRequest } from '@/api/use-request';
import { useSession } from '@/auth/session';
import { API_URL } from '@/config';
import { Badge, Body, Button, Card, KV, Loaded, Notice, Screen, Section, Title, when } from '@/ui';

export default function Security() {
  const { signOut } = useSession();
  const load = useCallback(() => api.GET('/api/v1/security'), []);
  const { data, error, loading, refreshing, reload, refetch } = useRequest(load);
  const [failure, setFailure] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  // A list that has landed since is the answer now: an old "Not signed out."
  // above it would describe a list the phone has already left.
  useEffect(() => setFailure(null), [data]);
  // Which phones are being signed out right now. The ref is what stops a
  // second tap: it lands before the re-render that would disable the button.
  const inFlight = useRef(new Set<number>());
  const [revoking, setRevoking] = useState<ReadonlySet<number>>(new Set());

  async function revoke(id: number) {
    if (inFlight.current.has(id)) return;
    inFlight.current.add(id);
    setRevoking(new Set(inFlight.current));
    setFailure(null);
    try {
      const { error: problem, response, thrown } = await api
        .DELETE('/api/v1/devices/{id}', { params: { path: { id } } })
        .then((result) => ({ ...result, thrown: undefined }))
        .catch((caught: unknown) => ({ error: undefined, response: undefined, thrown: caught }));
      // 404: this login's own phone, so it is already signed out - from the
      // portal, or by another tap. Either way it is gone, as asked.
      if (response?.status === 204 || response?.status === 404) refetch();
      else setFailure(describeError(problem, response, thrown));
    } finally {
      inFlight.current.delete(id);
      setRevoking(new Set(inFlight.current));
    }
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
    <Screen refreshing={refreshing} onRefresh={reload}>
      <Loaded data={data} error={error} loading={loading}>
        {(security) => (
          <>
            <Section title="Two-factor sign-in">
              <Card>
                {security.two_factor_enabled ? (
                  <>
                    <Badge tone="ok" label="On" />
                    <KV label="Recovery codes left">{security.recovery_codes_left}</KV>
                    <Body muted>
                      To turn it off, or to get new recovery codes (turn it off, then set it up again), use the
                      portal&apos;s Security page.
                    </Body>
                  </>
                ) : (
                  <>
                    <Badge tone="danger" label="Off" />
                    <Body>
                      Two-factor is off. This phone stays signed in, but you can&apos;t sign in to the app again until
                      you turn it back on at the portal&apos;s Security page.
                    </Body>
                  </>
                )}
              </Card>
            </Section>

            <Section title="Phones signed in to the app">
              <Body muted>
                Each of these can read your alerts and acknowledge them. If you do not recognise one, sign it out
                and change your password.
              </Body>
              {failure ? (
                <Notice tone="danger" title="Not signed out.">
                  {failure}
                </Notice>
              ) : null}
              {security.devices.map((device) => (
                <Card key={device.id}>
                  <Title>{device.name}</Title>
                  {device.current ? <Badge tone="ok" label="This phone" /> : null}
                  <KV label="Signed in">{when(device.signed_in_at)}</KV>
                  <KV label="Last active">{when(device.last_refreshed_at ?? device.signed_in_at)}</KV>
                  {device.current ? null : (
                    <Button
                      label="Sign out this phone"
                      variant="secondary"
                      busy={revoking.has(device.id)}
                      onPress={() => revoke(device.id)}
                    />
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
        {/* The portal's footer link (decision 27), opened in the browser. */}
        <Button label="Privacy policy" variant="secondary" onPress={() => Linking.openURL(`${API_URL}/privacy`)} />
        <Body muted>Alert channels, quiet hours, Telegram and your data are on the member portal.</Body>
      </Section>

      <Button label="Sign out of this phone" variant="danger" busy={leaving} onPress={confirmSignOut} />
    </Screen>
  );
}
