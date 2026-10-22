// The app's sign-in, held the way decision 21c says: a 15-minute access token
// in memory only, and a refresh token in the platform's secure storage
// (Keychain on iOS, Keystore on Android). Neither ever goes in a cookie.
import * as SecureStore from 'expo-secure-store';

import { API_URL } from '@/config';

const REFRESH_KEY = 'minas-guardian.refresh-token';
// Refresh a minute before the server's deadline, so no request leaves with a
// token that dies in flight.
const EARLY_MS = 60_000;

export type Tokens = {
  access_token: string;
  expires_in: number;
  refresh_token: string;
  device_id: number;
};

let access: { token: string; expiresAt: number; deviceId: number } | null = null;
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<(signedIn: boolean) => void>();

/** Called with false when the sign-in ends - revoked, expired, or signed
 * out - and true when a new one starts. Returns the unsubscribe. */
export function onSessionChange(listener: (signedIn: boolean) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function announce(signedIn: boolean) {
  listeners.forEach((listener) => listener(signedIn));
}

export async function storeTokens(tokens: Tokens, now = Date.now()): Promise<void> {
  access = { token: tokens.access_token, expiresAt: now + tokens.expires_in * 1000, deviceId: tokens.device_id };
  await SecureStore.setItemAsync(REFRESH_KEY, tokens.refresh_token);
  announce(true);
}

/** Drop the sign-in from this phone. The server side is ended by the caller
 * (DELETE /api/v1/session) or has already ended (a 401 on refresh). */
export async function forget(): Promise<void> {
  access = null;
  await SecureStore.deleteItemAsync(REFRESH_KEY);
  announce(false);
}

export async function hasStoredSession(): Promise<boolean> {
  return (await SecureStore.getItemAsync(REFRESH_KEY)) !== null;
}

export function currentDeviceId(): number | null {
  return access?.deviceId ?? null;
}

/** A usable access token, refreshing first if it is missing or nearly due.
 * Null when there is no sign-in, or the refresh could not happen now. */
export async function accessToken(now = Date.now()): Promise<string | null> {
  if (access && access.expiresAt - EARLY_MS > now) return access.token;
  return (await refresh()) && access ? access.token : null;
}

/** Trade the stored refresh token for a new pair - and only ever one trade
 * at a time. The server revokes a device whose replaced refresh token is
 * presented again (21c), so two requests refreshing at once with the same
 * token would sign the member out. Every caller waits on the same trade. */
export function refresh(): Promise<boolean> {
  refreshing ??= trade().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function trade(): Promise<boolean> {
  const stored = await SecureStore.getItemAsync(REFRESH_KEY);
  if (stored === null) {
    access = null;
    return false;
  }
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/session/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: stored }),
    });
  } catch {
    // Offline or unreachable: the sign-in is still good, so keep it and let
    // the request that needed it fail as a network problem.
    return false;
  }
  if (response.status === 401) {
    // Revoked, replayed, expired, or the password changed: it is over.
    await forget();
    return false;
  }
  if (!response.ok) return false; // the server's trouble, not the sign-in's
  await storeTokens((await response.json()) as Tokens);
  return true;
}

/** The fetch every authenticated API call goes through (openapi-fetch's
 * `fetch` option): the bearer token on the way out, and one refresh and one
 * retry if the server says the token is no longer good. */
export async function authedFetch(request: Request): Promise<Response> {
  const retry = request.clone();
  const token = await accessToken();
  if (token !== null) request.headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(request);
  if (response.status !== 401 || !(await refresh()) || access === null) return response;
  retry.headers.set('Authorization', `Bearer ${access.token}`);
  return fetch(retry);
}

/** For tests: start from nothing. */
export function resetForTests() {
  access = null;
  refreshing = null;
  listeners.clear();
}
