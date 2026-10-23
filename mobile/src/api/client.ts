// The typed API client. src/api/schema.d.ts is generated from api/openapi.yaml
// (`npm run gen:api`); the mobile CI regenerates it and fails on any
// difference, so the app is always built against the contract the server is
// tested against (decision 21f).
import createClient from 'openapi-fetch';

import { authedFetch } from '@/auth/tokens';
import { API_URL } from '@/config';

import { exchange, isTimeout } from './network';
import type { components, paths } from './schema';

export type Schemas = components['schemas'];

/** Every call that needs the member signed in. */
export const api = createClient<paths>({ baseUrl: API_URL, fetch: authedFetch });

// Signing in is given longer than a read: the server records the sign-in (and
// spends the code) before it answers, so giving up early costs the member it.
export const SIGN_IN_DEADLINE_MS = 45_000;

/** Signing in, which happens before there is anything to authenticate with. */
export const publicApi = createClient<paths>({
  baseUrl: API_URL,
  fetch: (request) => exchange(request, SIGN_IN_DEADLINE_MS),
});

const CANT_REACH = "Can't reach Minas right now. Check your connection and try again.";
const TIMED_OUT = "Minas didn't answer in time. Check your connection and try again.";
// Over HTTPS nothing on the way can answer as Minas, so this is a network
// that intercepts traffic, or an answer from Minas that broke on the way.
const UNREADABLE = "Minas's answer didn't arrive in a form this app can read. Try again in a moment, or on another network.";

/** A sentence a person can read, for any failed call. The server's own
 * message where it sent one (they are written for the member), otherwise a
 * plain statement of what went wrong - never an engine's error text. `thrown`
 * is what the call threw, when it threw instead of answering. */
export function describeError(error: unknown, response?: Response, thrown?: unknown): string {
  if (error && typeof error === 'object' && 'message' in error && typeof error.message === 'string') {
    return error.message;
  }
  // An answer that would not parse threw a SyntaxError.
  if (response === undefined) {
    if (thrown instanceof SyntaxError) return UNREADABLE;
    return isTimeout(thrown) ? TIMED_OUT : CANT_REACH;
  }
  return `Something went wrong on Minas's side (${response.status}). Try again in a moment.`;
}
