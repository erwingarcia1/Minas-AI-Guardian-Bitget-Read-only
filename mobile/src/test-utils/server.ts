/// <reference types="jest" />
// A fake Minas at the network boundary: the app's real client, token handling
// and screens run against it unchanged, the way they run against the server.
import { REFRESH_KEY, resetForTests } from '@/auth/tokens';

export { REFRESH_KEY };

/** The secure store's contents (src/test-utils/setup.ts). */
export const store = (jest.requireMock('expo-secure-store') as { __store: Map<string, string> }).__store;

type Handler = (request: Request, params: string[]) => Response | Promise<Response>;

const routes: [string, RegExp, Handler][] = [];
/** "METHOD /path" for every request, in order. */
export const calls: string[] = [];
/** Every request as sent: its query and whether it carried a token. */
export const sent: { call: string; query: string; authorization: string | null }[] = [];
/** The JSON body of the last request to each "METHOD /path". */
export const bodies: Record<string, unknown> = {};

/** Answer METHOD on a path ({id} matches a number); later routes win. */
export function on(method: string, pattern: string, handler: Handler) {
  routes.unshift([method, new RegExp(`^${pattern.replace(/\{id\}/g, '(\\d+)')}$`), handler]);
}

export function json(status: number, body?: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
  });
}

/** A route that never answers - until the app gives up on it. */
export function silence(): Promise<Response> {
  return new Promise(() => {});
}

/** A fetch that throws, as fetch does with no network. */
export function offline(): never {
  throw new TypeError('Network request failed');
}

async function handle(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const signal = init?.signal ?? request.signal;
  const url = new URL(request.url);
  const call = `${request.method} ${url.pathname}`;
  calls.push(call);
  sent.push({ call, query: url.search.replace(/^\?/, ''), authorization: request.headers.get('Authorization') });
  if (request.method !== 'GET') {
    const text = await request.clone().text();
    bodies[call] = text ? JSON.parse(text) : null;
  }
  const route = routes.find(([method, pattern]) => request.method === method && pattern.test(url.pathname));
  if (!route) return json(404, { error: 'not_found', message: 'There is no such API route.' });
  const answer = Promise.resolve(route[2](request, url.pathname.match(route[1])!.slice(1)));
  if (!signal) return answer;
  // Gives up when the app does, as a real connection would.
  return Promise.race([
    answer,
    new Promise<never>((_, reject) => {
      const abort = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
      if (signal.aborted) abort();
      else signal.addEventListener('abort', abort);
    }),
  ]);
}

/** Start each test from nothing: no routes, no requests, an empty store, no
 * sign-in in memory, and this fake as the network. */
export function resetServer() {
  routes.length = 0;
  calls.length = 0;
  sent.length = 0;
  for (const key of Object.keys(bodies)) delete bodies[key];
  store.clear();
  resetForTests();
  globalThis.fetch = jest.fn(handle) as typeof fetch;
}
