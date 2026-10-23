// The app's sign-in, held the way decision 21c says: a 15-minute access token
// in memory only, and a refresh token in the platform's secure storage
// (Keychain on iOS, Keystore on Android). Neither ever goes in a cookie.
import * as SecureStore from 'expo-secure-store';

import { exchange, within } from '@/api/network';
import { API_URL } from '@/config';

// One stored sign-in per server. A token is only ever presented to the server
// that issued it: pointing a development build at a laptop must not hand the
// laptop - over plain HTTP, on a shared Wi-Fi - the token for the real one.
// (Secure-store keys may hold only letters, digits, '.', '-' and '_'.)
export const REFRESH_KEY = `minas-guardian.refresh-token.${API_URL.replace(/[^\w.-]/g, '_')}`;

// This phone only, and only while it is unlocked: never restored onto another
// iPhone from a backup, never readable from a locked one.
const STORE: SecureStore.SecureStoreOptions = { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY };

// Refresh a minute before the server's deadline, so no request leaves with a
// token that dies in flight.
const EARLY_MS = 60_000;
// A refresh is given longer than other calls before the app stops waiting.
// Once the server has rotated, an answer that never arrives leaves this phone
// holding the replaced token, and presenting that again ends the sign-in.
const REFRESH_DEADLINE_MS = 30_000;
// Signing out tells the server if it answers this quickly; the phone forgets
// either way.
const SIGN_OUT_WAIT_MS = 5_000;

export type Tokens = {
  access_token: string;
  expires_in: number;
  refresh_token: string;
  device_id: number;
};

/** How a refresh went - and, when there is no token to send, why: none on this
 * phone, ended by the server, no answer from Minas, an answer that was an
 * error, or a secure store that would not give the token up. */
export type Outcome = 'ok' | 'none' | 'ended' | 'unreachable' | 'unavailable' | 'unreadable';

/** How a sign-in ended: the member signing out, or the server ending it. */
export type Ending = 'signed-out' | 'ended';

let access: { token: string; expiresAt: number; deviceId: number } | null = null;
// The newest refresh token, in memory as well as in the store, so this run
// always presents the token the server last issued - even if the store's
// write failed or has not finished.
let latest: string | null = null;
let refreshing: Promise<Outcome> | null = null;
// Bumped on every sign-out: a refresh still in flight then must not sign the
// phone back in.
let epoch = 0;
const listeners = new Set<(signedIn: boolean, ending?: Ending) => void>();

/** Called with false (and how) when the sign-in ends - revoked, expired, or
 * signed out - and true when a new one starts. Returns the unsubscribe. */
export function onSessionChange(listener: (signedIn: boolean, ending?: Ending) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function announce(signedIn: boolean, ending?: Ending) {
  listeners.forEach((listener) => listener(signedIn, ending));
}

export async function storeTokens(tokens: Tokens, now = Date.now()): Promise<void> {
  access = { token: tokens.access_token, expiresAt: now + tokens.expires_in * 1000, deviceId: tokens.device_id };
  latest = tokens.refresh_token;
  try {
    await SecureStore.setItemAsync(REFRESH_KEY, tokens.refresh_token, STORE);
  } catch {
    // The store refused the write. This run carries on with the token in
    // memory, and the next refresh writes again. What must not survive is the
    // token this one replaced: presented after a restart, the server would
    // take it for a stolen copy and end the sign-in (21c).
    await SecureStore.deleteItemAsync(REFRESH_KEY, STORE).catch(() => {});
  }
  announce(true);
}

/** Drop the sign-in from this phone. The server side is ended by signOut or
 * has already ended (a 401 on refresh). Never throws: leaving cannot fail. */
export async function forget(ending: Ending = 'signed-out'): Promise<void> {
  epoch += 1;
  access = null;
  latest = null;
  await SecureStore.deleteItemAsync(REFRESH_KEY, STORE).catch(() => {});
  announce(false, ending);
}

/** Whether this phone holds a sign-in for this server. A store that cannot be
 * read counts as none - the member signs in again - rather than leaving the
 * app on its splash screen. */
export async function hasStoredSession(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(REFRESH_KEY, STORE)) !== null;
  } catch {
    await SecureStore.deleteItemAsync(REFRESH_KEY, STORE).catch(() => {});
    return false;
  }
}

export function currentDeviceId(): number | null {
  return access?.deviceId ?? null;
}

/** A token to send, refreshing first if it is missing or nearly due - or why
 * there is none. */
async function usable(): Promise<{ token: string } | { token: null; outcome: Outcome }> {
  if (access && access.expiresAt - EARLY_MS > Date.now()) return { token: access.token };
  const outcome = await refresh();
  if (outcome === 'ok' && access) return { token: access.token };
  // The refresh failed, but the token held is only inside its last minute,
  // not dead: it still works.
  if (access && access.expiresAt > Date.now()) return { token: access.token };
  return { token: null, outcome: outcome === 'ok' ? 'none' : outcome };
}

/** A usable access token, or null when there is no sign-in or it cannot be
 * renewed right now. */
export async function accessToken(): Promise<string | null> {
  return (await usable()).token;
}

/** Trade the refresh token for a new pair - and only ever one trade at a
 * time. The server revokes a device whose replaced refresh token is presented
 * again (21c), so two requests refreshing at once with the same token would
 * sign the member out. Every caller waits on the same trade. */
export function refresh(): Promise<Outcome> {
  refreshing ??= trade().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function trade(): Promise<Outcome> {
  const started = epoch;
  let presented = latest;
  if (presented === null) {
    try {
      presented = await SecureStore.getItemAsync(REFRESH_KEY, STORE);
    } catch {
      return 'unreadable';
    }
  }
  if (presented === null) {
    access = null;
    return 'none';
  }
  let response: Response;
  try {
    response = await exchange(
      new Request(`${API_URL}/api/v1/session/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: presented }),
      }),
      REFRESH_DEADLINE_MS,
    );
  } catch {
    // No answer. Whether the server rotated before the line dropped cannot
    // be known from here, so the token held is kept and presented next time:
    // if the server did rotate, that ends the sign-in (21c; decision 21h
    // asks for the grace window that would close exactly this).
    return 'unreachable';
  }
  if (response.status === 401) {
    // Revoked, replayed, expired, or the password changed: it is over.
    if (epoch === started) await forget('ended');
    return 'ended';
  }
  if (!response.ok) return 'unavailable'; // the server's trouble, not the sign-in's
  let tokens: Tokens;
  try {
    tokens = (await response.json()) as Tokens;
  } catch {
    // A 200 that is not the API's answer - a Wi-Fi network's sign-in page,
    // say. The request never reached Minas, so the token held is still good.
    return 'unreachable';
  }
  if (epoch !== started) {
    // Signed out while this was in flight: end the pair just issued too.
    void endOnServer(tokens.access_token);
    return 'none';
  }
  await storeTokens(tokens);
  return 'ok';
}

/** What an authenticated call answers when there is no token to send it
 * with. It is never sent without one. */
function refused(outcome: Outcome): Response {
  if (outcome === 'unreachable') throw new TypeError('Network request failed');
  if (outcome === 'unavailable') return answer(503, "Minas couldn't renew this phone's sign-in just now. Try again in a moment.");
  if (outcome === 'unreadable') return answer(503, "This phone couldn't read its saved sign-in. Try again in a moment.");
  return answer(401, 'Sign in again.');
}

function answer(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: status === 401 ? 'unauthorized' : 'unavailable', message }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** The fetch every authenticated API call goes through (openapi-fetch's
 * `fetch` option): the bearer token on the way out, and one refresh and one
 * retry if the server says the token is no longer good. */
export async function authedFetch(request: Request): Promise<Response> {
  const retry = request.clone();
  const first = await usable();
  if (first.token === null) return refused(first.outcome);
  request.headers.set('Authorization', `Bearer ${first.token}`);
  const response = await exchange(request);
  if (response.status !== 401) return response;
  // The server no longer takes that token. Another call may already have
  // replaced it; if not, refresh - once - and retry once.
  let next = access !== null && access.token !== first.token ? access.token : null;
  if (next === null) {
    const outcome = await refresh();
    if (outcome === 'ended') return response;
    if (outcome !== 'ok' || access === null) return refused(outcome);
    next = access.token;
  }
  retry.headers.set('Authorization', `Bearer ${next}`);
  return exchange(retry);
}

/** Sign this phone out: on the server when it answers within a few seconds,
 * and on the phone whatever happens - leaving never waits on the network. */
export async function signOut(): Promise<void> {
  await within(
    SIGN_OUT_WAIT_MS,
    accessToken().then((token) => (token === null ? undefined : endOnServer(token))),
  );
  await forget('signed-out');
}

async function endOnServer(token: string): Promise<void> {
  await exchange(
    new Request(`${API_URL}/api/v1/session`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } }),
    SIGN_OUT_WAIT_MS,
  ).catch(() => {});
}

/** For tests: start from nothing. */
export function resetForTests() {
  access = null;
  latest = null;
  refreshing = null;
  epoch = 0;
  listeners.clear();
}
