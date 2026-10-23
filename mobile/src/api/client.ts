// The typed API client. src/api/schema.d.ts is generated from api/openapi.yaml
// (`npm run gen:api`); the mobile CI regenerates it and fails on any
// difference, so the app is always built against the contract the server is
// tested against (decision 21f).
import createClient from 'openapi-fetch';

import { authedFetch } from '@/auth/tokens';
import { API_URL } from '@/config';

import { exchange } from './network';
import type { components, paths } from './schema';

export type Schemas = components['schemas'];

/** Every call that needs the member signed in. */
export const api = createClient<paths>({ baseUrl: API_URL, fetch: authedFetch });

/** Signing in, which happens before there is anything to authenticate with. */
export const publicApi = createClient<paths>({ baseUrl: API_URL, fetch: (request) => exchange(request) });

const CANT_REACH = "Can't reach Minas right now. Check your connection and try again.";
const UNREADABLE =
  "Minas sent an answer this app can't read. If you're on a hotel or café Wi-Fi, sign in to the network first, then try again.";

/** A sentence a person can read, for any failed call. The server's own
 * message where it sent one (they are written for the member), otherwise a
 * plain statement of what went wrong - never an engine's error text. `thrown`
 * is what the call threw, when it threw instead of answering. */
export function describeError(error: unknown, response?: Response, thrown?: unknown): string {
  if (error && typeof error === 'object' && 'message' in error && typeof error.message === 'string') {
    return error.message;
  }
  // An answer that would not parse threw a SyntaxError: something between the
  // phone and Minas answered instead of Minas.
  if (response === undefined) return thrown instanceof SyntaxError ? UNREADABLE : CANT_REACH;
  return `Something went wrong on Minas's side (${response.status}). Try again in a moment.`;
}
