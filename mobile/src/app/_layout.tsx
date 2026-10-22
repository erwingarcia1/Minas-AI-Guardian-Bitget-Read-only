import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';

import { SessionProvider, useSession } from '@/auth/session';
import { colors } from '@/theme';

// The splash stays up until we know whether this phone holds a sign-in, so
// nobody sees the sign-in screen flash past on the way to their accounts.
SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  return (
    <SessionProvider>
      <StatusBar style="light" />
      <Navigator />
    </SessionProvider>
  );
}

function Navigator() {
  const { state } = useSession();

  useEffect(() => {
    if (state !== 'loading') SplashScreen.hideAsync();
  }, [state]);

  if (state === 'loading') return null;
  const signedIn = state === 'signed-in';

  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        headerTitleStyle: { color: colors.text },
        contentStyle: { backgroundColor: colors.bg },
      }}>
      {/* Every screen with a member's data is behind the sign-in guard; when
          the sign-in ends (revoked, expired, signed out) the router leaves. */}
      <Stack.Protected guard={signedIn}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="alert/[id]" options={{ title: 'Incident' }} />
        <Stack.Screen name="activity" options={{ title: 'Who has touched your accounts' }} />
        <Stack.Screen name="limits" options={{ title: 'What the Guardian can do' }} />
      </Stack.Protected>
      <Stack.Protected guard={!signedIn}>
        <Stack.Screen name="sign-in" options={{ headerShown: false }} />
      </Stack.Protected>
    </Stack>
  );
}
