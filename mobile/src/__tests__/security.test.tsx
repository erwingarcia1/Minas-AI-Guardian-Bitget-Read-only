/// <reference types="jest" />
/**
 * The Security tab: two-factor as the server reports it, the phones signed in
 * to this login, the links out, and signing this phone out.
 */
import { act, fireEvent, screen, testRouter, waitFor } from 'expo-router/testing-library';
import { Alert, AppState } from 'react-native';

import { LIMITS, onPressOf, SECURITY, signedIn, SIGN_IN_LINE, useDemoServer } from '@/test-utils/app';
import { calls, json, offline, on, REFRESH_KEY, silence, store } from '@/test-utils/server';

jest.setTimeout(30_000);

useDemoServer();

async function openSecurity() {
  await signedIn();
  await fireEvent.press(screen.getByRole('button', { name: /^Security, tab/ }));
}

/** Answer "Sign out" in the confirmation, as a member would. */
function confirmEverything() {
  return jest.spyOn(Alert, 'alert').mockImplementation((_title, _message, buttons) => {
    buttons?.find((b) => b.style === 'destructive')?.onPress?.();
  });
}

test('two-factor on shows the codes left and where to change it', async () => {
  await openSecurity();
  expect(await screen.findByText('On')).toBeTruthy();
  expect(screen.getByText('8')).toBeTruthy();
});

test('two-factor off says so - not "On" - and what it means for this phone', async () => {
  on('GET', '/api/v1/security', () => json(200, { ...SECURITY, two_factor_enabled: false, recovery_codes_left: 0 }));
  await openSecurity();
  expect(await screen.findByText('Off')).toBeTruthy();
  expect(screen.queryByText('On')).toBeNull();
  expect(screen.getByText(/^Two-factor is off\. This phone stays signed in/)).toBeTruthy();
  expect(screen.queryByText('Recovery codes left')).toBeNull();
});

test('another phone can be signed out, and this one cannot be signed out from its own list', async () => {
  await openSecurity();
  expect(await screen.findByText('Old tablet')).toBeTruthy();
  const buttons = screen.getAllByRole('button', { name: 'Sign out this phone' });
  expect(buttons).toHaveLength(1); // only for the other phone
  on('GET', '/api/v1/security', () => json(200, { ...SECURITY, devices: [SECURITY.devices[0]] }));
  await fireEvent.press(buttons[0]);
  await waitFor(() => expect(screen.queryByText('Old tablet')).toBeNull());
  expect(calls).toContain('DELETE /api/v1/devices/9');
});

test('a second tap before the first has even re-rendered the button sends nothing more', async () => {
  on('DELETE', '/api/v1/devices/{id}', () => new Promise<Response>((resolve) => setTimeout(() => resolve(json(204)), 1_000)));
  await openSecurity();
  const button = await screen.findByRole('button', { name: 'Sign out this phone' });
  on('GET', '/api/v1/security', () => json(200, { ...SECURITY, devices: [SECURITY.devices[0]] }));
  await act(async () => {
    // Both taps inside one act: React renders nothing in between, so the
    // second lands on the same, still-enabled button - the race a fast
    // double tap is.
    const tap = onPressOf(button);
    const taps = [tap(), tap()];
    await jest.advanceTimersByTimeAsync(1_000);
    await Promise.all(taps);
  });
  await waitFor(() => expect(screen.queryByText('Old tablet')).toBeNull());
  expect(calls.filter((c) => c === 'DELETE /api/v1/devices/9')).toHaveLength(1);
  expect(screen.queryByText('Not signed out.')).toBeNull();
});

test('a phone already signed out elsewhere is simply gone, not an error', async () => {
  on('DELETE', '/api/v1/devices/{id}', () => json(404, { error: 'not_found', message: 'There is no such device.' }));
  await openSecurity();
  on('GET', '/api/v1/security', () => json(200, { ...SECURITY, devices: [SECURITY.devices[0]] }));
  await fireEvent.press(await screen.findByRole('button', { name: 'Sign out this phone' }));
  await waitFor(() => expect(screen.queryByText('Old tablet')).toBeNull());
  expect(screen.queryByText('Not signed out.')).toBeNull();
});

test('a sign-out the server refuses says why', async () => {
  on('DELETE', '/api/v1/devices/{id}', () => json(500));
  await openSecurity();
  await fireEvent.press(await screen.findByRole('button', { name: 'Sign out this phone' }));
  expect(await screen.findByText('Not signed out.')).toBeTruthy();
  expect(screen.getByText("Something went wrong on Minas's side (500). Try again in a moment.")).toBeTruthy();
});

test('the links from Security open the activity record and the limits page', async () => {
  await openSecurity();
  await fireEvent.press(await screen.findByRole('button', { name: 'Who has touched your accounts' }));
  expect(await screen.findByText('Phone app signed in')).toBeTruthy();
  await testRouter.back('/settings');
  await fireEvent.press(await screen.findByRole('button', { name: 'What the Guardian can and cannot do' }));
  expect(await screen.findByText(LIMITS.headline.title)).toBeTruthy();
  expect(screen.getByText(LIMITS.lead)).toBeTruthy();
  expect(screen.getByText(LIMITS.why.text)).toBeTruthy();
  expect(screen.getByText(/we cannot be faster than the source we read\.$/)).toBeTruthy();
});

test('coming back to the app reads the tab again', async () => {
  const registered = (AppState.addEventListener as jest.Mock).mock.calls.length;
  await openSecurity();
  await screen.findByText('Old tablet');
  on('GET', '/api/v1/security', () => json(200, { ...SECURITY, devices: [SECURITY.devices[0]] }));
  // The app returns to the front: every listener the screens registered hears it.
  const listeners = (AppState.addEventListener as jest.Mock).mock.calls
    .slice(registered)
    .filter(([event]) => event === 'change')
    .map(([, listener]) => listener as (state: string) => void);
  await act(async () => listeners.forEach((listener) => listener('active')));
  await waitFor(() => expect(screen.queryByText('Old tablet')).toBeNull());
});

test('signing out asks first, ends the sign-in on the server and on the phone, and returns to sign-in', async () => {
  const confirm = confirmEverything();
  await openSecurity();
  await fireEvent.press(await screen.findByRole('button', { name: 'Sign out of this phone' }));
  expect(confirm).toHaveBeenCalled();
  expect(await screen.findByText(SIGN_IN_LINE)).toBeTruthy();
  expect(calls).toContain('DELETE /api/v1/session');
  expect(store.has(REFRESH_KEY)).toBe(false);
  expect(screen.queryByText('This phone was signed out.')).toBeNull(); // their own choice: no warning
  confirm.mockRestore();
});

test.each([
  ['with no network', offline],
  ['with a server that never answers', silence],
])('signing out %s still signs the phone out', async (_how, answer) => {
  on('DELETE', '/api/v1/session', answer);
  const confirm = confirmEverything();
  await openSecurity();
  await fireEvent.press(await screen.findByRole('button', { name: 'Sign out of this phone' }));
  await act(() => jest.advanceTimersByTimeAsync(5_000));
  expect(await screen.findByText(SIGN_IN_LINE)).toBeTruthy();
  expect(store.has(REFRESH_KEY)).toBe(false);
  confirm.mockRestore();
});

test('a "Not signed out." notice goes once the list has moved on', async () => {
  on('DELETE', '/api/v1/devices/{id}', () => json(500));
  await openSecurity();
  await fireEvent.press(await screen.findByRole('button', { name: 'Sign out this phone' }));
  expect(await screen.findByText('Not signed out.')).toBeTruthy();
  // The tablet is signed out from the portal; the member looks away and back.
  on('GET', '/api/v1/security', () => json(200, { ...SECURITY, devices: [SECURITY.devices[0]] }));
  await fireEvent.press(screen.getByRole('button', { name: /^Accounts, tab/ }));
  await fireEvent.press(screen.getByRole('button', { name: /^Security, tab/ }));
  await waitFor(() => expect(screen.queryByText('Old tablet')).toBeNull());
  expect(screen.queryByText('Not signed out.')).toBeNull();
});
