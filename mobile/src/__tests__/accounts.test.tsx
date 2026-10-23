/// <reference types="jest" />
/**
 * "Am I being watched?" - the first screen, and the one that must never show
 * an old or invented "yes".
 */
import { act, fireEvent, screen, testRouter, waitFor } from 'expo-router/testing-library';

import { ACCOUNTS, openSignedIn, pullToRefresh, REFUSED_REASON, signedIn, useDemoServer } from '@/test-utils/app';
import { calls, json, offline, on } from '@/test-utils/server';

jest.setTimeout(30_000);

useDemoServer();

const accounts = (extra: object) => () => json(200, { ...ACCOUNTS, ...extra });

test.each([
  ['watching', "You're being watched.", 'Every connected account was checked within the last few minutes.'],
  ['degraded', 'Some of your accounts are not being watched.',
    'Monitoring is running, but not every account is being checked. The ones affected are marked below.'],
  ['not_watching', 'Your accounts are NOT being watched.',
    'Movements on them are not currently being detected. Minas has been alerted; what each account needs is marked below.'],
])('the %s verdict is the portal\'s, word for word', async (overall, title, detail) => {
  on('GET', '/api/v1/accounts', accounts({ overall }));
  await openSignedIn();
  expect(await screen.findByText(title)).toBeTruthy();
  expect(screen.getByText(detail)).toBeTruthy();
});

test('a verdict this version has no words for is never shown as good news', async () => {
  on('GET', '/api/v1/accounts', accounts({ overall: 'partially_paused' }));
  await openSignedIn();
  expect(await screen.findByText("This app can't tell whether you're being watched.")).toBeTruthy();
});

test('each account says Watching, Paused or Trouble - and what is wrong, where there is trouble', async () => {
  on('GET', '/api/v1/accounts', accounts({
    accounts: [
      ...ACCOUNTS.accounts,
      { id: 3, name: 'OKX - paused', exchange: 'okx', status: 'disabled', watched: false,
        reason: 'monitoring is switched off for this account', last_success_at: null, role: 'non-primary' },
    ],
  }));
  await openSignedIn();
  expect(await screen.findByText('Watching')).toBeTruthy();
  expect(screen.getByText('Trouble')).toBeTruthy();
  expect(screen.getByText('Paused')).toBeTruthy();
  expect(screen.getByText(REFUSED_REASON)).toBeTruthy(); // trouble says what is wrong
  expect(screen.queryByText('monitoring is switched off for this account')).toBeNull(); // a pause is a choice
  expect(screen.getByText('Never')).toBeTruthy();
  expect(screen.getByText('You are the emergency contact on this account: you can read and acknowledge.')).toBeTruthy();
});

test('a login with no accounts is told so, without a false alarm', async () => {
  on('GET', '/api/v1/accounts', accounts({ overall: 'not_watching', accounts: [] }));
  await openSignedIn();
  expect(await screen.findByText('No accounts yet — contact the Minas team.')).toBeTruthy();
  expect(screen.queryByText('Your accounts are NOT being watched.')).toBeNull();
});

test('an answer too old to trust is not shown as a verdict', async () => {
  await signedIn();
  on('GET', '/api/v1/accounts', offline); // every later check fails
  await act(() => jest.advanceTimersByTimeAsync(240_000)); // past the 225-second window
  expect(await screen.findByText('Checking again…')).toBeTruthy();
  expect(screen.queryByText('Some of your accounts are not being watched.')).toBeNull();
});

test('the tab asks again every 30 seconds while it is in view', async () => {
  await signedIn();
  const before = calls.filter((c) => c === 'GET /api/v1/accounts').length;
  await act(() => jest.advanceTimersByTimeAsync(61_000));
  expect(calls.filter((c) => c === 'GET /api/v1/accounts').length).toBe(before + 2);
});

test.each([
  ['a server error', () => json(500), "Something went wrong on Minas's side (500). Try again in a moment."],
  ['no network', offline, "Can't reach Minas right now. Check your connection and try again."],
  ['a page that is not the API', () => new Response('<html>Hotel Wi-Fi</html>', { status: 200 }),
    "Minas sent an answer this app can't read. If you're on a hotel or café Wi-Fi, sign in to the network first, then try again."],
  ['the server\'s own words', () => json(400, { error: 'invalid_request', message: 'The server says no.' }), 'The server says no.'],
])('%s is said plainly', async (_what, answer, sentence) => {
  on('GET', '/api/v1/accounts', answer);
  await openSignedIn();
  expect(await screen.findByText('Could not load this.')).toBeTruthy();
  expect(screen.getByText(sentence)).toBeTruthy();
});

test('a refresh that fails keeps what was shown, saying it is the last known', async () => {
  await signedIn();
  on('GET', '/api/v1/accounts', () => json(503));
  await pullToRefresh();
  expect(await screen.findByText('Showing what we had last.')).toBeTruthy();
  expect(screen.getByText('Binance - savings')).toBeTruthy();
  await waitFor(() => expect(screen.getByText("Something went wrong on Minas's side (503). Try again in a moment.")).toBeTruthy());
});

test('the incident nobody has acknowledged comes first, one tap from its record, and goes once acknowledged', async () => {
  const app = await signedIn();
  await fireEvent.press(screen.getByRole('link', { name: /^Needs your attention — incident #42\./ }));
  expect(app.getPathname()).toBe('/alert/42');
  await fireEvent.press(await screen.findByRole('button', { name: 'Acknowledge alert' }));
  await screen.findByText(/^By demo@minas\.example at/);
  await testRouter.back('/');
  await waitFor(() => expect(screen.queryByText('Needs your attention — incident #42')).toBeNull());
});

test('a pause about to stop monitoring is said first, with how to stop it', async () => {
  on('GET', '/api/v1/accounts', accounts({
    pending_pauses: [{ account_id: 2, account_name: 'Bitget - trading', execute_at: '2026-10-23T10:00:00+00:00' }],
  }));
  await openSignedIn();
  expect(await screen.findByText('Monitoring is about to stop.')).toBeTruthy();
  expect(screen.getByText(/^Bitget - trading at .*\. Cancel it on the member portal, on the account's page/)).toBeTruthy();
});

test('trouble says what can be done about it, and by whom', async () => {
  const trouble = (id: number, cause: string, role: string) => ({ id, name: `Account ${id}`, exchange: 'bitget',
    status: 'active', watched: false, reason: `reason ${id}`, cause, last_success_at: null, role });
  on('GET', '/api/v1/accounts', accounts({ accounts: [
    trouble(1, 'key_rejected', 'primary'),
    trouble(2, 'key_rejected', 'non-primary'),
    trouble(3, 'no_recent_check', 'primary'),
    trouble(4, 'tamper_suspected', 'primary'),
  ] }));
  await openSignedIn();
  expect(await screen.findByText("You can fix this: replace the API key on the member portal, on this account's page.")).toBeTruthy();
  expect(screen.getByText('The account holder can fix this by replacing the API key on the member portal.')).toBeTruthy();
  expect(screen.getAllByText('If this lasts, contact the Minas team.')).toHaveLength(1); // not for the investigation
});
