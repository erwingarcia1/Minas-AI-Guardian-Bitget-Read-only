// Every call to Minas has a deadline. React Native's fetch has none of its
// own - Expo Go and the platforms' HTTP clients are set up with no timeout -
// so without one a dropped Wi-Fi or a server that stops answering holds a
// spinner, and every request queued behind a sign-in refresh, for minutes.
export const DEADLINE_MS = 15_000;

/** Send one request and read its whole answer inside the deadline: a
 * connection that stalls after the headers would otherwise hang as long as one
 * that never connects. Resolves to a Response whose body has already arrived;
 * rejects, as fetch does, when no answer came in time. */
export async function exchange(request: Request, ms = DEADLINE_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    const response = await fetch(request, { signal: controller.signal });
    const body = await response.text();
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
