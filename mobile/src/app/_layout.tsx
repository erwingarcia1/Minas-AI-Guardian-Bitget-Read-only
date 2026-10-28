import { DarkTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';

import { SessionProvider, useSession } from '@/auth/session';
import { colors } from '@/theme';

// The splash stays up until we know whether this phone holds a sign-in, so
// nobody sees the welcome screen flash past on the way to their accounts.
SplashScreen.preventAutoHideAsync();

// The navigator's own surfaces (headers, the back menu, the space behind a
// screen) in the portal's colours, not React Navigation's light defaults.
const theme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    background: colors.bg,
    card: colors.surface,
    border: colors.border,
    text: colors.text,
    primary: colors.gold,
    notification: colors.danger,
  },
};

export default function RootLayout() {
  return (
    <SessionProvider>
      <ThemeProvider value={theme}>
        <StatusBar style="light" />
        <Navigator />
      </ThemeProvider>
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
        // A pushed screen can be reached from any tab, so its back button
        // says "Back" rather than naming one (or the route group, "(tabs)").
        headerBackTitle: 'Back',
        contentStyle: { backgroundColor: colors.bg },
      }}>
      {/* Every screen with a member's data is behind the sign-in guard; when
          the sign-in ends (revoked, expired, signed out) the router leaves for
          the welcome screen, and the member screens leave the stack.

          How the guard falls back, read in node_modules/expo-router 58.0.25
          (paths under build/), because the result must be /landing - not the
          sign-in form, and never a member screen:
          - layouts/withLayoutContext.js, useFilterScreenChildren: a screen
            under a Protected whose guard is false STAYS registered with the
            navigator; its name goes into guardedRedirects together with the
            Protected's redirectTo.
          - useScreens.js, getQualifiedRouteComponent (its BaseRoute):
            useGuardRedirect(value.route) answers for a guarded screen, and
            BaseRoute renders a <Redirect> (link/Redirect.js: router.replace
            on focus) in place of the screen's component; routeToScreen only
            wires that component in, through getComponent. So a cold start on
            a protected URL (the initial "/" with no sign-in) and a guard
            flipping false while a member screen is open both end on the
            redirect's target.
          - layouts/GuardContext.js, resolveGuardRedirects: that target is
            redirectTo, else the navigator's first unguarded route - the
            layout's anchor if it has one, else node.children sorted by
            sortRoutes.js: groups and index first, then the SHORTER route
            name, ties in file order. The order of the Stack.Screen lines
            below plays no part in it. "landing" and "sign-in" tie on length,
            so the target is named here with redirectTo instead of being
            left to an alphabetical tie-break.
          - layouts/useClearGuardedRoutes.js calls the navigator's
            removeRoutes with every guarded name (handed in by
            fork/native-stack/createNativeStackNavigator.js), which
            layouts/createNativeStackProps.js dispatches as REMOVE_ROUTES,
            reduced in react-navigation/routers/StackRouter.js: every other
            guarded route leaves the stack, and the focused one is what the
            Redirect replaces. After an ended sign-in the stack is [landing]
            alone, with nothing to go back to. */}
      <Stack.Protected guard={signedIn} redirectTo="/landing">
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="alert/[id]" options={{ title: 'Incident' }} />
        <Stack.Screen name="activity" options={{ title: 'Who has touched your accounts' }} />
        <Stack.Screen name="limits" options={{ title: 'What the Guardian can and cannot do' }} />
      </Stack.Protected>
      {/* Without a sign-in: the welcome screen first, and the form behind its
          "Sign in" button, with a header so the back button returns to it.
          Once signed in, whichever is open gives way to the accounts. */}
      <Stack.Protected guard={!signedIn} redirectTo="/">
        <Stack.Screen name="landing" options={{ title: 'Welcome', headerShown: false }} />
        {/* Singular: a fast double tap on the welcome screen's "Sign in" is
            two pushes before the form has covered the button, and a PUSH
            (react-navigation/routers/StackRouter.js) reuses a route already
            in the stack only through getId; useScreens.js getSortedChildren
            turns dangerouslySingular into that getId, so the second push
            finds the form there and one Back returns to the welcome screen. */}
        <Stack.Screen name="sign-in" options={{ title: 'Sign in' }} dangerouslySingular />
        {/* Decision 17 (2026-10-28): the invitation Minas emails, accepted
            here - the code, a password and two-factor make the login. */}
        <Stack.Screen name="accept-invitation" options={{ title: 'Accept your invitation' }} dangerouslySingular />
      </Stack.Protected>
    </Stack>
  );
}
