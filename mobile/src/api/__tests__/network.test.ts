/// <reference types="jest" />
/**
 * exchange(): one request, its whole answer, inside a deadline the app keeps
 * itself - and an answer that broke on the way is never taken for one.
 */
import { exchange, isTimeout } from '../network';

const URL = 'https://guardian.minastrade.com/api/v1/accounts';

function answering(response: () => Response) {
  globalThis.fetch = jest.fn(async () => response()) as typeof fetch;
}

afterEach(() => {
  jest.useRealTimers();
});

test('an answer whose body never finishes still gives up at the deadline', async () => {
  jest.useFakeTimers();
  // The headers arrive; the rest never does (on Android, what a cancel that
  // lost the race with the answer looks like).
  answering(() => new Response(new ReadableStream({ start() {} }), { status: 200 }));
  const call = exchange(new Request(URL));
  const outcome = call.then(
    () => 'answered',
    (thrown: unknown) => (isTimeout(thrown) ? 'timed out' : String(thrown)),
  );
  await jest.advanceTimersByTimeAsync(15_000);
  expect(await outcome).toBe('timed out');
});

test('an answer shorter than the length the server sent is a dropped connection, not an answer', async () => {
  answering(() => new Response('{"total": 3, "aler', { status: 200, headers: { 'Content-Length': '200' } }));
  await expect(exchange(new Request(URL))).rejects.toThrow(TypeError);
});

test('a whole answer is passed on as it came, multi-byte text included', async () => {
  // One-, two-, three- and four-byte characters: the length the server sends counts bytes.
  const body = JSON.stringify({ reason: 'still firing — repeated 3× since, café', amount: '0.85 ₿', mark: '🔒' });
  answering(() => new Response(body, { status: 200, headers: { 'Content-Length': String(new TextEncoder().encode(body).length) } }));
  const response = await exchange(new Request(URL));
  expect(response.status).toBe(200);
  expect(await response.text()).toBe(body);
});

test("a compressed answer's length is not judged against the text", async () => {
  answering(() => new Response('{}', { status: 200, headers: { 'Content-Length': '900', 'Content-Encoding': 'gzip' } }));
  expect((await exchange(new Request(URL))).status).toBe(200);
});

test('an answer with no body at all is passed on as such', async () => {
  answering(() => new Response(null, { status: 204 }));
  const response = await exchange(new Request(URL, { method: 'DELETE' }));
  expect(response.status).toBe(204);
  expect(await response.text()).toBe('');
});
