/// <reference types="jest" />
/**
 * Signing in, staying signed in, and being signed out - the guard every
 * member-data screen sits behind - with the app's real routes against the
 * fake server.
 */
import * as SecureStore from 'expo-secure-store';
import * as SplashScreen from 'expo-splash-screen';
import { act, fireEvent, renderRouter, screen, testRouter, waitFor } from 'expo-router/testing-library';

import { onPressOf, openSignedIn, pullToRefresh, signedIn, SIGN_IN_LINE, useDemoServer } from '@/test-utils/app';
import { bodies, calls, json, offline, on, REFRESH_KEY, silence, store } from '@/test-utils/server';

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(async () => true),
  hideAsync: jest.fn(async () => undefined),
}));
jest.setTimeout(30_000);

useDemoServer();

async function fillIn(email: string, password: string, code: string) {
  await fireEvent.changeText(await screen.findByLabelText('Email'), email);
  await fireEvent.changeText(screen.getByLabelText('Password'), password);
  await fireEvent.changeText(screen.getByLabelText('Code from your authenticator app'), code);
  await fireEvent.press(screen.getByRole('button', { name: 'Sign in' }));
}

test('with no sign-in on the phone, the app opens on sign-in, asking nothing of the server', async () => {
  const app = await renderRouter('./src/app', { initialUrl: '/' });
  expect(await screen.findByText(SIGN_IN_LINE)).toBeTruthy();
  expect(app.getPathname()).toBe('/sign-in');
  expect(calls).toEqual([]);
  expect(SplashScreen.hideAsync).toHaveBeenCalled();
});

test("signing in lands on the accounts, in the portal's words, sending what was typed trimmed", async () => {
  const app = await renderRouter('./src/app', { initialUrl: '/' });
  await fillIn(' demo@minas.example ', ' minas-demo-password ', ' 123456 ');
  expect(await screen.findByText('Some of your accounts are not being watched.')).toBeTruthy();
  expect(app.getPathname()).toBe('/');
  expect(store.get(REFRESH_KEY)).toBe('refresh-1');
  // The password is sent as typed: spaces can be part of one.
  expect(bodies['POST /api/v1/session']).toEqual({ email: 'demo@minas.example', password: ' minas-demo-password ',
    code: '123456', device_name: 'Test phone' });
});

test.each([
  [401, 'invalid_credentials', 'That email, password or code was not accepted.', 'Not signed in.'],
  [403, 'two_factor_required', "Turn on two-factor sign-in on the portal's Security page, then sign in here.",
    'Two-factor sign-in is needed first.'],
  [429, 'too_many_attempts', 'Too many attempts. Try again later.', 'Not signed in.'],
])('a refused sign-in (%i %s) shows the server\'s own sentence and stores nothing', async (status, error, message, title) => {
  on('POST', '/api/v1/session', () => json(status, { error, message }));
  const app = await renderRouter('./src/app', { initialUrl: '/' });
  await fillIn('demo@minas.example', 'wrong', '000000');
  expect(await screen.findByText(message)).toBeTruthy();
  expect(screen.getByText(title)).toBeTruthy();
  expect(app.getPathname()).toBe('/sign-in');
  expect(store.has(REFRESH_KEY)).toBe(false);
});

test('signing in with no network says so, plainly', async () => {
  on('POST', '/api/v1/session', offline);
  await renderRouter('./src/app', { initialUrl: '/' });
  await fillIn('demo@minas.example', 'minas-demo-password', '123456');
  expect(await screen.findByText("Can't reach Minas right now. Check your connection and try again.")).toBeTruthy();
  expect(store.has(REFRESH_KEY)).toBe(false);
});

test("a server without the app API is named as that, not as a connection problem", async () => {
  on('POST', '/api/v1/session', () => new Response('<html>Not found</html>', { status: 404 }));
  await renderRouter('./src/app', { initialUrl: '/' });
  await fillIn('demo@minas.example', 'minas-demo-password', '123456');
  expect(await screen.findByText("This server doesn't offer the app's sign-in.")).toBeTruthy();
});

test('the code field takes a recovery code too, and says so', async () => {
  await renderRouter('./src/app', { initialUrl: '/' });
  const field = await screen.findByLabelText('Code from your authenticator app');
  expect(field.props.keyboardType).toBeUndefined(); // the full keyboard: recovery codes have letters
  expect(screen.getByText('Or one of your recovery codes.')).toBeTruthy();
  await fillIn('demo@minas.example', 'minas-demo-password', 'ABCD-EFGH-IJKL');
  await screen.findByText('Some of your accounts are not being watched.');
  expect((bodies['POST /api/v1/session'] as { code: string }).code).toBe('ABCD-EFGH-IJKL');
});

test('a stored sign-in opens straight on the accounts, refreshing first', async () => {
  await signedIn();
  expect(calls.slice(0, 2)).toEqual(['POST /api/v1/session/refresh', 'GET /api/v1/accounts']);
  expect(store.get(REFRESH_KEY)).toBe('refresh-2'); // the rotated token is the one kept
});

test('a sign-in the server has ended sends the app back to sign-in, saying why', async () => {
  on('POST', '/api/v1/session/refresh', () => json(401, { error: 'invalid_refresh_token', message: 'Sign in again.' }));
  await openSignedIn();
  expect(await screen.findByText('This phone was signed out.')).toBeTruthy();
  expect(screen.getByText(SIGN_IN_LINE)).toBeTruthy();
  expect(store.has(REFRESH_KEY)).toBe(false);
});

test('a sign-in ended while in use leaves the incident for sign-in, with no way back, and signing in again works', async () => {
  const app = await openSignedIn('/alert/42');
  expect(await screen.findByText('1. Revoke any API key you do not recognise.')).toBeTruthy();
  // Signed out from the portal: everything this phone holds is now refused.
  on('GET', '/api/v1/alerts/{id}', () => json(401, { error: 'unauthorized', message: 'Sign in again.' }));
  on('POST', '/api/v1/session/refresh', () => json(401, { error: 'invalid_refresh_token', message: 'Sign in again.' }));
  await pullToRefresh();
  expect(await screen.findByText('This phone was signed out.')).toBeTruthy();
  expect(app.getPathname()).toBe('/sign-in');
  expect(testRouter.canGoBack()).toBe(false);
  expect(store.has(REFRESH_KEY)).toBe(false);

  await fillIn('demo@minas.example', 'minas-demo-password', '123456');
  expect(await screen.findByText('Some of your accounts are not being watched.')).toBeTruthy();
  expect(app.getPathname()).toBe('/');
  expect(screen.queryByText('This phone was signed out.')).toBeNull();
});

test('a secure store that cannot be read at launch opens on sign-in, not a splash screen forever', async () => {
  store.set(REFRESH_KEY, 'refresh-1');
  (SecureStore.getItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Could not decrypt'));
  const app = await renderRouter('./src/app', { initialUrl: '/' });
  expect(await screen.findByText(SIGN_IN_LINE)).toBeTruthy();
  expect(app.getPathname()).toBe('/sign-in');
  await waitFor(() => expect(SplashScreen.hideAsync).toHaveBeenCalled());
});

test('a sign-in Minas does not answer in time says the code may already be spent, not "check your connection"', async () => {
  on('POST', '/api/v1/session', silence);
  await renderRouter('./src/app', { initialUrl: '/' });
  await fireEvent.changeText(await screen.findByLabelText('Email'), 'demo@minas.example');
  await fireEvent.changeText(screen.getByLabelText('Password'), 'minas-demo-password');
  await fireEvent.changeText(screen.getByLabelText('Code from your authenticator app'), '123456');
  const button = screen.getByRole('button', { name: 'Sign in' });
  // The tap settles only at the 45-second deadline: tap, and run the clock, in one act.
  await act(async () => {
    const tapped = onPressOf(button)();
    await jest.advanceTimersByTimeAsync(45_000);
    await tapped;
  });
  expect(await screen.findByText(/^Minas didn't answer in time\. Your sign-in may still have gone through: wait for the next code/)).toBeTruthy();
  expect(store.has(REFRESH_KEY)).toBe(false);
});

test('a sign-in answered with something that is not a token pair stores nothing and says so', async () => {
  on('POST', '/api/v1/session', () => json(200, { welcome: 'to the hotel' }));
  await renderRouter('./src/app', { initialUrl: '/' });
  await fillIn('demo@minas.example', 'minas-demo-password', '123456');
  expect(await screen.findByText("Minas's answer didn't arrive in a form this app can read. Try again in a moment, or on another network.")).toBeTruthy();
  expect(store.has(REFRESH_KEY)).toBe(false);
});

test('a sign-in Minas answers slowly, inside the sign-in deadline, still signs in', async () => {
  on('POST', '/api/v1/session', () => new Promise<Response>((resolve) => setTimeout(() => resolve(json(200, {
    token_type: 'Bearer', access_token: 'access-1', expires_in: 900, refresh_token: 'refresh-1',
    refresh_expires_in: 2592000, device_id: 7 })), 20_000)));
  await renderRouter('./src/app', { initialUrl: '/' });
  await fireEvent.changeText(await screen.findByLabelText('Email'), 'demo@minas.example');
  await fireEvent.changeText(screen.getByLabelText('Password'), 'minas-demo-password');
  await fireEvent.changeText(screen.getByLabelText('Code from your authenticator app'), '123456');
  const button = screen.getByRole('button', { name: 'Sign in' });
  await act(async () => {
    const tapped = onPressOf(button)();
    await jest.advanceTimersByTimeAsync(20_000); // past a read's 15 s
    await tapped;
  });
  expect(await screen.findByText('Some of your accounts are not being watched.')).toBeTruthy();
  expect(store.get(REFRESH_KEY)).toBe('refresh-1');
});
