// The typed API client. src/api/schema.d.ts is generated from api/openapi.yaml
// (`npm run gen:api`); the mobile CI regenerates it and fails on any
// difference, so the app is always built against the contract the server is
// tested against (decision 21f).
import createClient from 'openapi-fetch';

import { authedFetch } from '@/auth/tokens';
import { API_URL } from '@/config';

import type { components, paths } from './schema';

export type Schemas = components['schemas'];

/** Every call that needs the member signed in. */
export const api = createClient<paths>({ baseUrl: API_URL, fetch: authedFetch });

/** Signing in, which happens before there is anything to authenticate with. */
export const publicApi = createClient<paths>({ baseUrl: API_URL });

/** A sentence a person can read, for any failed call. The server's own
 * message where it sent one (they are written for the member), otherwise a
 * plain statement of what went wrong. */
export function describeError(error: unknown, response?: Response): string {
  if (error && typeof error === 'object' && 'message' in error && typeof error.message === 'string') {
    return error.message;
  }
  if (response === undefined) return "Can't reach Minas right now. Check your connection and try again.";
  return `Something went wrong on Minas's side (${response.status}). Try again in a moment.`;
}
