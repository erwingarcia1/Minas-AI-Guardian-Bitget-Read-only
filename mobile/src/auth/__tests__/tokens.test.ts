/// <reference types="jest" />
/**
 * The app's half of decision 21c. The server revokes a device whose replaced
 * refresh token is presented again, so the one thing the app must never do
 * is present the same refresh token twice - not even from two requests that
 * notice an expired access token at the same moment.
 */
import * as SecureStore from 'expo-secure-store';

import { API_URL } from '@/config';
import { json, offline, on, REFRESH_KEY, resetServer, sent, silence, store } from '@/test-utils/server';

import { accessToken, authedFetch, hasStoredSession, onSessionChange, refresh, signOut, storeTokens } from '../tokens';

const ACCOUNTS_URL = `${API_URL}/api/v1/accounts`;

function tokens(n: number) {
  return { access_token: `access-${n}`, expires_in: 900, refresh_token: `refresh-${n}`, device_id: 7 };
}

/** The server's own rule: refresh tokens rotate, a replayed one is refused,
 * and only the newest access token is taken - so a test that replays fails
 * here as it would in production. */
function rotatingServer() {
  let current = 'refresh-1';
  let issued = 1;
  const presented: string[] = [];
  on('POST', '/api/v1/session/refresh', async (request) => {
    const { refresh_token } = (await request.json()) as { refresh_token: string };
    presented.push(refresh_token);
    if (refresh_token !== current) return json(401, { error: 'invalid_refresh_token', message: 'Sign in again.' });
    issued += 1;
    current = `refresh-${issued}`;
    return json(200, tokens(issued));
  });
  on('GET', '/api/v1/accounts', (request) =>
    request.headers.get('Authorization') === `Bearer access-${issued}`
      ? json(200, { ok: true })
      : json(401, { error: 'unauthorized', message: 'Sign in again.' }),
  );
  on('DELETE', '/api/v1/session', () => json(204));
  return { presented };
}

function sessionChanges() {
  const changes: string[] = [];
  onSessionChange((signedIn, ending) => changes.push(signedIn ? 'signed-in' : `signed-out:${ending}`));
  return changes;
}

const requestsWithoutToken = () => sent.filter((r) => !r.call.includes('/session') && r.authorization === null);

beforeEach(() => {
  resetServer();
});

afterEach(() => {
  jest.useRealTimers();
});

// --- the one refresh -------------------------------------------------------------------

test('a fresh access token is used as it is, with no refresh', async () => {
  const server = rotatingServer();
  await storeTokens(tokens(1));
  expect(await accessToken()).toBe('access-1');
  expect(server.presented).toEqual([]);
});

test('requests that find the token expired together share ONE refresh', async () => {
  const server = rotatingServer();
  await storeTokens(tokens(1), Date.now() - 3_600_000); // issued an hour ago: expired
  const results = await Promise.all([accessToken(), accessToken(), accessToken(), refresh()]);
  expect(results).toEqual(['access-2', 'access-2', 'access-2', 'ok']);
  expect(server.presented).toEqual(['refresh-1']); // presented once, never replayed
  expect(store.get(REFRESH_KEY)).toBe('refresh-2'); // the rotated one is kept
});

test('a token the server no longer accepts is refreshed once and the call retried once', async () => {
  const server = rotatingServer();
  // The phone thinks access-0 is good; the server has moved on (revoked
  // access tokens are refused even before their 15 minutes are up).
  await storeTokens({ ...tokens(0), refresh_token: 'refresh-1' });
  const response = await authedFetch(new Request(ACCOUNTS_URL));
  expect(response.status).toBe(200);
  expect(server.presented).toEqual(['refresh-1']);
  expect(sent.map((r) => r.call)).toEqual(['GET /api/v1/accounts', 'POST /api/v1/session/refresh', 'GET /api/v1/accounts']);
});

test('every API call carries the bearer token, never a cookie', async () => {
  rotatingServer();
  await storeTokens(tokens(1));
  expect((await authedFetch(new Request(ACCOUNTS_URL))).status).toBe(200);
  const request = (globalThis.fetch as jest.Mock).mock.calls[0][0] as Request;
  expect(request.headers.get('Authorization')).toBe('Bearer access-1');
  expect(request.headers.get('Cookie')).toBeNull();
});

// --- when there is no token to send ----------------------------------------------------

test('with no sign-in at all, nothing is refreshed and nothing is sent', async () => {
  const server = rotatingServer();
  expect(await accessToken()).toBeNull();
  const response = await authedFetch(new Request(ACCOUNTS_URL));
  expect(response.status).toBe(401);
  expect(server.presented).toEqual([]);
  expect(sent).toEqual([]);
});

test('a refresh the server refuses ends the sign-in on this phone, and says the server ended it', async () => {
  on('POST', '/api/v1/session/refresh', () => json(401, { error: 'invalid_refresh_token', message: 'Sign in again.' }));
  const changes = sessionChanges();
  store.set(REFRESH_KEY, 'revoked');
  expect(await refresh()).toBe('ended');
  expect(store.has(REFRESH_KEY)).toBe(false);
  expect(changes).toEqual(['signed-out:ended']);
});

test('being offline is not being signed out - and the call fails as a connection problem, unsent', async () => {
  on('POST', '/api/v1/session/refresh', offline);
  const changes = sessionChanges();
  store.set(REFRESH_KEY, 'refresh-1');
  await expect(authedFetch(new Request(ACCOUNTS_URL))).rejects.toThrow(TypeError);
  expect(store.get(REFRESH_KEY)).toBe('refresh-1');
  expect(changes).toEqual([]);
  expect(requestsWithoutToken()).toEqual([]);
});

test('a server error on refresh keeps the sign-in, and the call says so instead of going out unsigned', async () => {
  on('POST', '/api/v1/session/refresh', () => json(503, {}));
  store.set(REFRESH_KEY, 'refresh-1');
  const response = await authedFetch(new Request(ACCOUNTS_URL));
  expect(response.status).toBe(503);
  expect(((await response.json()) as { message: string }).message).toBe(
    "Minas couldn't renew this phone's sign-in just now. Try again in a moment.",
  );
  expect(sent.map((r) => r.call)).toEqual(['POST /api/v1/session/refresh']); // one try, and the call never sent
  expect(store.get(REFRESH_KEY)).toBe('refresh-1');
});

test('a token in its last minute is still used when it cannot be renewed', async () => {
  on('POST', '/api/v1/session/refresh', () => json(503, {}));
  on('GET', '/api/v1/accounts', () => json(200, { ok: true }));
  await storeTokens(tokens(1), Date.now() - 870_000); // 30 seconds left
  expect((await authedFetch(new Request(ACCOUNTS_URL))).status).toBe(200);
  expect(sent.at(-1)?.authorization).toBe('Bearer access-1');
});

test.each([
  ['a page that is not the API', () => new Response('<html>Welcome to Hotel Wi-Fi</html>', { status: 200 })],
  ['JSON of another shape', () => json(200, { hello: 'world' })],
])('a refresh answered 200 with %s keeps the sign-in, stores nothing and is said to be unreadable', async (_what, answer) => {
  on('POST', '/api/v1/session/refresh', answer);
  store.set(REFRESH_KEY, 'refresh-1');
  expect(await refresh()).toBe('garbled');
  expect(store.get(REFRESH_KEY)).toBe('refresh-1');
  await expect(authedFetch(new Request(ACCOUNTS_URL))).rejects.toThrow(SyntaxError); // the unreadable sentence
  expect(sent.filter((r) => r.authorization !== null)).toEqual([]); // never 'Bearer undefined'
});

// --- what is stored, and where -----------------------------------------------------------

test('the refresh token is kept per server, on this phone only, while it is unlocked', async () => {
  rotatingServer();
  await storeTokens(tokens(1));
  expect(REFRESH_KEY).toBe(`minas-guardian.refresh-token.${API_URL.replace(/[^\w.-]/g, '_')}`);
  expect(REFRESH_KEY).toMatch(/^[\w.-]+$/); // the only characters the store accepts
  expect(SecureStore.setItemAsync).toHaveBeenLastCalledWith(REFRESH_KEY, 'refresh-1', {
    keychainAccessible: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
  });
});

test('a token stored for another server is never presented to this one', async () => {
  const server = rotatingServer();
  // Built from this server's own address, so it can never be this one's key.
  store.set(`minas-guardian.refresh-token.${`${API_URL}.other`.replace(/[^\w.-]/g, '_')}`, 'refresh-for-the-laptop');
  expect(await hasStoredSession()).toBe(false);
  expect(await refresh()).toBe('none');
  expect(server.presented).toEqual([]);
});

test('if the store will not save the new token, this run presents it and the replaced one is not left behind', async () => {
  const server = rotatingServer();
  store.set(REFRESH_KEY, 'refresh-1');
  (SecureStore.setItemAsync as jest.Mock).mockRejectedValueOnce(new Error('The keychain is unavailable'));
  expect(await refresh()).toBe('ok');
  // Presented after a restart, refresh-1 would look stolen (21c): it is gone.
  expect(store.has(REFRESH_KEY)).toBe(false);
  expect(await refresh()).toBe('ok'); // and this run carries on with refresh-2 from memory
  expect(server.presented).toEqual(['refresh-1', 'refresh-2']);
  expect(store.get(REFRESH_KEY)).toBe('refresh-3'); // the next save worked
});

test('a store that cannot be read at launch counts as signed out, and the unreadable entry is dropped', async () => {
  store.set(REFRESH_KEY, 'refresh-1');
  (SecureStore.getItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Could not decrypt'));
  expect(await hasStoredSession()).toBe(false);
  expect(store.has(REFRESH_KEY)).toBe(false);
});

// --- deadlines and signing out -------------------------------------------------------------

test('a call with no answer gives up after 15 seconds, as a connection problem', async () => {
  jest.useFakeTimers();
  on('GET', '/api/v1/accounts', silence);
  await storeTokens(tokens(1));
  const call = authedFetch(new Request(ACCOUNTS_URL));
  const outcome = expect(call).rejects.toMatchObject({ name: 'TimeoutError' });
  await jest.advanceTimersByTimeAsync(15_000);
  await outcome;
});

test('a refresh with no answer gives up after 30 seconds and keeps the sign-in', async () => {
  jest.useFakeTimers();
  on('POST', '/api/v1/session/refresh', silence);
  store.set(REFRESH_KEY, 'refresh-1');
  const trade = refresh();
  await jest.advanceTimersByTimeAsync(29_000);
  expect(sent).toHaveLength(1); // still waiting: no second presentation
  await jest.advanceTimersByTimeAsync(1_000);
  expect(await trade).toBe('unreachable');
  expect(store.get(REFRESH_KEY)).toBe('refresh-1');
});

test('signing out ends the sign-in on the server, then on the phone', async () => {
  rotatingServer();
  await storeTokens(tokens(1));
  const changes = sessionChanges();
  await signOut();
  expect(sent.map((r) => `${r.call} ${r.authorization}`)).toEqual(['DELETE /api/v1/session Bearer access-1']);
  expect(store.has(REFRESH_KEY)).toBe(false);
  expect(changes).toEqual(['signed-out:signed-out']);
});

test('signing out with no answer from the server still signs the phone out, within 5 seconds', async () => {
  jest.useFakeTimers();
  on('DELETE', '/api/v1/session', silence);
  await storeTokens(tokens(1));
  const leaving = signOut();
  await jest.advanceTimersByTimeAsync(5_000);
  await leaving;
  expect(store.has(REFRESH_KEY)).toBe(false);
  expect(await accessToken()).toBeNull();
});

test('a refresh still in flight when the phone signs out does not sign it back in', async () => {
  jest.useFakeTimers();
  let answer: (response: Response) => void = () => {};
  on('POST', '/api/v1/session/refresh', () => new Promise<Response>((resolve) => (answer = resolve)));
  on('DELETE', '/api/v1/session', () => json(204));
  store.set(REFRESH_KEY, 'refresh-1');
  const changes = sessionChanges();
  const trade = refresh();
  const leaving = signOut(); // its own token request waits on the same trade...
  await jest.advanceTimersByTimeAsync(5_000); // ...gives up after 5 seconds, and the phone forgets
  await leaving;
  answer(json(200, tokens(2))); // then the server answers the refresh after all
  expect(await trade).toBe('none');
  expect(store.has(REFRESH_KEY)).toBe(false);
  expect(await accessToken()).toBeNull();
  expect(changes).toEqual(['signed-out:signed-out']);
  // The pair the server issued is ended there too.
  expect(sent.filter((r) => r.call === 'DELETE /api/v1/session').map((r) => r.authorization)).toEqual(['Bearer access-2']);
});

// --- one sign-in never lends its token to another -----------------------------------------

test.each([
  ['times out', 'timeout'],
  ['is answered with a new pair', 'ok'],
  ['is refused', 'refused'],
])('signing out during a stuck refresh, then in again: when the old refresh %s, nothing goes out on the new token', async (_how, ending) => {
  jest.useFakeTimers();
  let answer: (response: Response) => void = () => {};
  on('POST', '/api/v1/session/refresh', () => new Promise<Response>((resolve) => (answer = resolve)));
  on('GET', '/api/v1/accounts', () => json(200, { ok: true }));
  on('DELETE', '/api/v1/session', () => json(204));
  store.set(REFRESH_KEY, 'refresh-1');
  const old = authedFetch(new Request(ACCOUNTS_URL)); // waits on the refresh
  const leaving = signOut(); // so does sign-out's own token request
  await jest.advanceTimersByTimeAsync(5_000);
  await leaving; // the phone has forgotten
  await storeTokens(tokens(9)); // and the member signs in again
  if (ending === 'timeout') await jest.advanceTimersByTimeAsync(30_000);
  else answer(ending === 'ok' ? json(200, tokens(2)) : json(401, { error: 'invalid_refresh_token', message: 'x' }));
  await old.catch(() => undefined);
  await jest.advanceTimersByTimeAsync(0);
  expect(sent.filter((r) => r.authorization === 'Bearer access-9')).toEqual([]);
  expect(store.get(REFRESH_KEY)).toBe('refresh-9'); // the new sign-in stands
  expect(await accessToken()).toBe('access-9');
});

test("a new sign-in's refresh does not wait on the old sign-in's stuck one", async () => {
  jest.useFakeTimers();
  on('POST', '/api/v1/session/refresh', async (request) => {
    const { refresh_token } = (await request.json()) as { refresh_token: string };
    return refresh_token === 'refresh-1' ? silence() : json(200, tokens(10));
  });
  store.set(REFRESH_KEY, 'refresh-1');
  void refresh(); // the old sign-in's, stuck
  const leaving = signOut();
  await jest.advanceTimersByTimeAsync(5_000);
  await leaving;
  await storeTokens(tokens(9), Date.now() - 3_600_000); // a new sign-in whose access token is already due
  expect(await accessToken()).toBe('access-10'); // its own refresh, answered at once
});
