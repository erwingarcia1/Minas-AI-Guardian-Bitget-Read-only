// Every call to Minas has a deadline. React Native's fetch has none of its
// own - Expo Go and the platforms' HTTP clients are set up with no timeout -
// so without one a dropped Wi-Fi or a server that stops answering holds a
// spinner, and every request queued behind a sign-in refresh, for minutes.
export const DEADLINE_MS = 15_000;

/** What a call rejects with when Minas did not answer in time. */
export function timedOut(): Error {
  return Object.assign(new Error('Minas did not answer in time.'), { name: 'TimeoutError' });
}

export function isTimeout(thrown: unknown): boolean {
  return thrown instanceof Error && thrown.name === 'TimeoutError';
}

/** Send one request and read its whole answer inside the deadline: a
 * connection that stalls after the headers would otherwise hang as long as one
 * that never connects. Resolves to a Response whose body has already arrived;
 * rejects, as fetch does, when there is no complete answer - with timedOut()
 * when the deadline passed. */
export async function exchange(request: Request, ms = DEADLINE_MS): Promise<Response> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  // The deadline is kept here, in JavaScript, not left to the platform's
  // cancellation: on Android an answer that lands as the deadline fires can
  // leave the read waiting for an end that never comes.
  const gaveUp = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(timedOut());
      controller.abort();
    }, ms);
  });
  try {
    const response = await Promise.race([fetch(request, { signal: controller.signal }), gaveUp]);
    const body = await Promise.race([response.text(), gaveUp]);
    // Android hands back what arrived before a dropped connection as if it
    // were the whole answer. Shorter than the server said is not an answer.
    if (cutShort(response, body)) throw new TypeError('Network request failed');
    return new Response(body === '' || NO_BODY.has(response.status) ? null : body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  } finally {
    clearTimeout(timer);
  }
}

// Statuses a Response may not be built with a body for.
const NO_BODY = new Set([101, 204, 205, 304]);

/** Whether the body is shorter than the Content-Length the server sent. Not
 * judged for a compressed answer, whose length counts the compressed bytes. */
function cutShort(response: Response, body: string): boolean {
  const declared = response.headers.get('Content-Length');
  if (declared === null || NO_BODY.has(response.status) || response.headers.get('Content-Encoding')) return false;
  const length = Number(declared);
  return Number.isFinite(length) && utf8Length(body) < length;
}

/** The UTF-8 byte length of a string: what Content-Length counts. */
function utf8Length(text: string): number {
  let bytes = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      bytes += 4; // a surrogate pair is one four-byte character
      i += 1;
    } else bytes += 3;
  }
  return bytes;
}

/** Wait for `work`, but never longer than `ms`: for the one thing that must
 * finish whatever the network does - signing out of the phone. */
export async function within(ms: number, work: Promise<unknown>): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, ms);
  });
  try {
    await Promise.race([work.then(() => undefined, () => undefined), deadline]);
  } finally {
    clearTimeout(timer);
  }
}
