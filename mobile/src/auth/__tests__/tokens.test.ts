/// <reference types="jest" />
/**
 * The app's half of decision 21c. The server revokes a device whose replaced
 * refresh token is presented again, so the one thing the app must never do
 * is present the same refresh token twice - not even from two requests that
 * notice an expired access token at the same moment.
 */
import * as SecureStore from 'expo-secure-store';

import { accessToken, authedFetch, onSessionChange, refresh, resetForTests, storeTokens } from '../tokens';

jest.mock('expo-secure-store', () => {
  const store = new Map<string, string>();
  return {
    __store: store,
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => void store.set(key, value)),
    deleteItemAsync: jest.fn(async (key: string) => void store.delete(key)),
  };
});

const store = (SecureStore as unknown as { __store: Map<string, string> }).__store;
const REFRESH_URL = 'https://guardian.minastrade.com/api/v1/session/refresh';

function tokens(n: number) {
  return { access_token: `access-${n}`, expires_in: 900, refresh_token: `refresh-${n}`, device_id: 7 };
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

/** A fake server: refresh tokens rotate, and a replayed one is refused -
 * the server's own rule, so a test that replays would fail here as it would
 * in production. */
function fakeServer() {
  let current = 'refresh-1';
  let issued = 1;
  const presented: string[] = [];
  const fetchMock = jest.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    if (request.url === REFRESH_URL) {
      const { refresh_token } = JSON.parse(await request.text());
      presented.push(refresh_token);
      if (refresh_token !== current) return json(401, { error: 'invalid_refresh_token', message: 'Sign in again.' });
      issued += 1;
      current = `refresh-${issued}`;
      return json(200, tokens(issued));
    }
    const auth = request.headers.get('Authorization');
    return auth === `Bearer access-${issued}` ? json(200, { ok: true }) : json(401, { error: 'unauthorized', message: 'x' });
  });
  return { fetchMock, presented };
}

beforeEach(() => {
  resetForTests();
  store.clear();
});

test('a fresh access token is used as it is, with no refresh', async () => {
  const server = fakeServer();
  globalThis.fetch = server.fetchMock as typeof fetch;
  await storeTokens(tokens(1));
  expect(await accessToken()).toBe('access-1');
  expect(server.presented).toEqual([]);
});

test('requests that find the token expired together share ONE refresh', async () => {
  const server = fakeServer();
  globalThis.fetch = server.fetchMock as typeof fetch;
  await storeTokens(tokens(1), Date.now() - 3_600_000); // issued an hour ago: expired
  const results = await Promise.all([accessToken(), accessToken(), accessToken(), refresh()]);
  expect(results.slice(0, 3)).toEqual(['access-2', 'access-2', 'access-2']);
  expect(server.presented).toEqual(['refresh-1']); // presented once, never replayed
  expect(store.get('minas-guardian.refresh-token')).toBe('refresh-2'); // the rotated one is kept
});

test('a refresh the server refuses ends the sign-in on this phone', async () => {
  globalThis.fetch = jest.fn(async () => json(401, { error: 'invalid_refresh_token', message: 'Sign in again.' })) as typeof fetch;
  const changes: boolean[] = [];
  onSessionChange((signedIn) => changes.push(signedIn));
  store.set('minas-guardian.refresh-token', 'revoked');
  expect(await refresh()).toBe(false);
  expect(store.has('minas-guardian.refresh-token')).toBe(false);
  expect(changes).toEqual([false]);
});

test('being offline is not being signed out', async () => {
  globalThis.fetch = jest.fn(async () => {
    throw new TypeError('Network request failed');
  }) as typeof fetch;
  const changes: boolean[] = [];
  onSessionChange((signedIn) => changes.push(signedIn));
  store.set('minas-guardian.refresh-token', 'refresh-1');
  expect(await refresh()).toBe(false);
  expect(store.get('minas-guardian.refresh-token')).toBe('refresh-1');
  expect(changes).toEqual([]);
});

test('a server error on refresh keeps the sign-in too', async () => {
  globalThis.fetch = jest.fn(async () => json(503, {})) as typeof fetch;
  store.set('minas-guardian.refresh-token', 'refresh-1');
  expect(await refresh()).toBe(false);
  expect(store.get('minas-guardian.refresh-token')).toBe('refresh-1');
});

test('every API call carries the bearer token, never a cookie', async () => {
  const server = fakeServer();
  globalThis.fetch = server.fetchMock as typeof fetch;
  await storeTokens(tokens(1));
  const response = await authedFetch(new Request('https://guardian.minastrade.com/api/v1/accounts'));
  expect(response.status).toBe(200);
  const sent = server.fetchMock.mock.calls[0][0] as Request;
  expect(sent.headers.get('Authorization')).toBe('Bearer access-1');
  expect(sent.headers.get('Cookie')).toBeNull();
});

test('a token the server no longer accepts is refreshed once and the call retried once', async () => {
  const server = fakeServer();
  globalThis.fetch = server.fetchMock as typeof fetch;
  // The phone thinks access-0 is good; the server has moved on (revoked
  // access tokens are refused even before their 15 minutes are up).
  await storeTokens({ ...tokens(0), refresh_token: 'refresh-1' });
  const response = await authedFetch(new Request('https://guardian.minastrade.com/api/v1/accounts'));
  expect(response.status).toBe(200);
  expect(server.presented).toEqual(['refresh-1']);
  const calls = server.fetchMock.mock.calls.map(([input]) => (input instanceof Request ? input.url : String(input)));
  expect(calls).toEqual([
    'https://guardian.minastrade.com/api/v1/accounts',
    REFRESH_URL,
    'https://guardian.minastrade.com/api/v1/accounts',
  ]);
});

test('with no sign-in at all, nothing is refreshed and no token is sent', async () => {
  const server = fakeServer();
  globalThis.fetch = server.fetchMock as typeof fetch;
  expect(await accessToken()).toBeNull();
  const response = await authedFetch(new Request('https://guardian.minastrade.com/api/v1/accounts'));
  expect(response.status).toBe(401);
  expect(server.presented).toEqual([]);
});
