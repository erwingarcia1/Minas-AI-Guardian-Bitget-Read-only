/// <reference types="jest" />
/**
 * The welcome screen: what a phone with no sign-in opens on, where an ended
 * sign-in lands and what it is told there, and the one way to the sign-in
 * form - with the app's real routes against the fake server.
 */
import * as SecureStore from 'expo-secure-store';
import * as SplashScreen from 'expo-splash-screen';
import { act, fireEvent, renderRouter, screen, testRouter, waitFor } from 'expo-router/testing-library';
import { AccessibilityInfo } from 'react-native';

import { LANDING_STATEMENT, LIMITS, onPressOf, openSignedIn, pullToRefresh, SIGN_IN_LINE, useDemoServer } from '@/test-utils/app';
import { calls, json, on, REFRESH_KEY, store } from '@/test-utils/server';

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(async () => true),
  hideAsync: jest.fn(async () => undefined),
}));
jest.setTimeout(30_000);

useDemoServer();

const ENDED = 'This phone was signed out.';
const ADVICE = '1. Revoke any API key you do not recognise.';
/** What a screen reader is made to say unprompted (the mock keeps every call). */
const announced = AccessibilityInfo.announceForAccessibilityWithOptions as jest.Mock;

/** Sign in on the form, submitting from the code field (its "go" key): the
 * form's button and the welcome screen's are both named "Sign in". */
async function signInWith(email: string, password: string, code: string) {
  await fireEvent.changeText(await screen.findByLabelText('Email'), email);
  await fireEvent.changeText(screen.getByLabelText('Password'), password);
  const codeField = screen.getByLabelText('Code from your authenticator app');
  await fireEvent.changeText(codeField, code);
  await fireEvent(codeField, 'submitEditing');
}

test('with no sign-in on the phone, the app opens on the welcome screen, asking nothing of the server', async () => {
  announced.mockClear();
  const app = await renderRouter('./src/app', { initialUrl: '/' });
  expect(await screen.findByText(LANDING_STATEMENT)).toBeTruthy();
  expect(app.getPathname()).toBe('/landing');
  // What the Guardian is, and is not, in the portal's words.
  expect(screen.getByText(LIMITS.headline.title)).toBeTruthy();
  expect(screen.getByText('How it works')).toBeTruthy();
  expect(screen.getByText('What it cannot do')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
  expect(screen.queryByText(ENDED)).toBeNull();
  expect(calls).toEqual([]);
  expect(SplashScreen.hideAsync).toHaveBeenCalled();
  // To a screen reader: the statement is the heading the portal's <h1> is,
  // and the standing headline is read in place, not announced over it.
  expect(screen.getByRole('header', { name: LANDING_STATEMENT })).toBeTruthy();
  expect(announced).not.toHaveBeenCalled();
});

test('"Sign in" leads to the form, which has a way back to the welcome screen', async () => {
  const app = await renderRouter('./src/app', { initialUrl: '/' });
  await fireEvent.press(await screen.findByRole('button', { name: 'Sign in' }));
  expect(await screen.findByText(SIGN_IN_LINE)).toBeTruthy();
  expect(app.getPathname()).toBe('/sign-in');
  expect(testRouter.canGoBack()).toBe(true);
  await testRouter.back('/landing');
  expect(calls).toEqual([]);
});

test('a fast double tap on "Sign in" opens one form, and one Back returns to the welcome screen', async () => {
  const app = await renderRouter('./src/app', { initialUrl: '/' });
  const button = await screen.findByRole('button', { name: 'Sign in' });
  await act(async () => {
    // Both taps inside one act: the second lands before the pushed form
    // has covered the button, as a fast double tap does.
    const tap = onPressOf(button);
    tap();
    tap();
  });
  expect(await screen.findByText(SIGN_IN_LINE)).toBeTruthy();
  expect(app.getPathname()).toBe('/sign-in');
  await testRouter.back('/landing');
  expect(testRouter.canGoBack()).toBe(false); // one form was behind the button, not two
});

test('a sign-in the server has ended lands on the welcome screen, saying why, with no way back', async () => {
  on('POST', '/api/v1/session/refresh', () => json(401, { error: 'invalid_refresh_token', message: 'Sign in again.' }));
  announced.mockClear();
  const app = await openSignedIn();
  expect(await screen.findByText(ENDED)).toBeTruthy();
  expect(screen.getByText(LANDING_STATEMENT)).toBeTruthy();
  expect(app.getPathname()).toBe('/landing');
  expect(testRouter.canGoBack()).toBe(false);
  expect(store.has(REFRESH_KEY)).toBe(false);
  // Said aloud once, and alone: the headline below it is not an event.
  expect(announced).toHaveBeenCalledTimes(1);
  expect(announced).toHaveBeenCalledWith(expect.stringMatching(/^This phone was signed out\. That happens when/), { queue: true });
});

test('a sign-in ended while in use leaves the incident for the welcome screen, with no way back, and signing in again works', async () => {
  const app = await openSignedIn('/alert/42');
  expect(await screen.findByText(ADVICE)).toBeTruthy();
  // Signed out from the portal: everything this phone holds is now refused.
  on('GET', '/api/v1/alerts/{id}', () => json(401, { error: 'unauthorized', message: 'Sign in again.' }));
  on('POST', '/api/v1/session/refresh', () => json(401, { error: 'invalid_refresh_token', message: 'Sign in again.' }));
  await pullToRefresh();
  expect(await screen.findByText(ENDED)).toBeTruthy();
  expect(app.getPathname()).toBe('/landing');
  expect(testRouter.canGoBack()).toBe(false);
  expect(screen.queryByText(ADVICE)).toBeNull(); // the incident has left the stack
  expect(store.has(REFRESH_KEY)).toBe(false);

  await fireEvent.press(screen.getByRole('button', { name: 'Sign in' }));
  await signInWith('demo@minas.example', 'minas-demo-password', '123456');
  expect(await screen.findByText('Some of your accounts are not being watched.')).toBeTruthy();
  expect(app.getPathname()).toBe('/');
  expect(screen.queryByText(ENDED)).toBeNull();
  expect(screen.queryByText(LANDING_STATEMENT)).toBeNull();
  expect(testRouter.canGoBack()).toBe(false);
});

test('a secure store that cannot be read at launch opens on the welcome screen, not a splash screen forever', async () => {
  store.set(REFRESH_KEY, 'refresh-1');
  (SecureStore.getItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Could not decrypt'));
  const app = await renderRouter('./src/app', { initialUrl: '/' });
  expect(await screen.findByText(LANDING_STATEMENT)).toBeTruthy();
  expect(app.getPathname()).toBe('/landing');
  expect(screen.queryByText(ENDED)).toBeNull(); // nothing ended it: there was nothing to read
  await waitFor(() => expect(SplashScreen.hideAsync).toHaveBeenCalled());
});
