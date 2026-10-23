/// <reference types="jest" />
/**
 * Alerts and incidents: the list, its older pages, one incident, and the
 * app's one account-facing write - acknowledge.
 */
import { act, fireEvent, screen, testRouter, waitFor } from 'expo-router/testing-library';

import { alertSummary, onPressOf, openSignedIn, pullToRefresh, server as demo, signedIn, useDemoServer } from '@/test-utils/app';
import { calls, json, on, sent, silence } from '@/test-utils/server';

jest.setTimeout(30_000);

useDemoServer();

const ROW = /^Withdrawal #\d+ to a new address$/;

/** Text on screen, top to bottom. */
function textsInOrder(): string[] {
  return screen.container
    .queryAll((node) => node.type === 'Text')
    .map((node) => [node.props.children].flat().filter((c) => typeof c === 'string').join(''));
}

/** The titles of the stack's headers (rendered natively, so not as text). */
function headerTitles(): string[] {
  return screen.container.queryAll((node) => node.type === 'RNSScreenStackHeaderConfig').map((node) => node.props.title);
}

async function openAlertsTab() {
  await fireEvent.press(screen.getByRole('button', { name: /^Alerts, tab/ }));
}

test('an incident reads before it acts: what to do, then the button, then the record', async () => {
  const app = await openSignedIn('/alert/42');
  expect(await screen.findByText('1. Revoke any API key you do not recognise.')).toBeTruthy(); // numbered, as in the email
  expect(app.getPathname()).toBe('/alert/42');
  expect(headerTitles()).toContain('Incident #42'); // the header names it
  expect(screen.getByText(/1\.20000000/)).toBeTruthy(); // the exact stored quantity, never a float
  const order = textsInOrder();
  const at = (text: string) => order.findIndex((t) => t.startsWith(text));
  expect(at('1. Revoke any API key')).toBeLessThan(at('Minas cannot do this for you.'));
  expect(at('Minas cannot do this for you.')).toBeLessThan(at('Acknowledge alert'));
  expect(at('Acknowledge alert')).toBeLessThan(at('Timeline'));
  // Not acknowledged yet: the timeline marks it for attention.
  expect(screen.getByText('Not acknowledged')).toBeTruthy();
  expect(screen.getAllByText('Attention')).toHaveLength(1);
});

test('acknowledging shows who at once, and the list says so on the way back', async () => {
  await signedIn();
  await openAlertsTab();
  expect(await screen.findByText('Not acknowledged')).toBeTruthy();
  await fireEvent.press(screen.getByText('Withdrawal #42 to a new address'));
  await fireEvent.press(await screen.findByRole('button', { name: 'Acknowledge alert' }));
  expect(await screen.findByText(/^By demo@minas\.example at/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Acknowledge alert' })).toBeNull();
  expect(calls).toContain('POST /api/v1/alerts/42/acknowledge');

  await testRouter.back('/alerts');
  expect(await screen.findByText('Acknowledged by demo@minas.example')).toBeTruthy();
  expect(screen.queryByText('Not acknowledged')).toBeNull();
  expect(calls.filter((c) => c === 'GET /api/v1/alerts')).toHaveLength(2); // read again on return
});

test('an alert that reached nobody, or is still firing, says so in the list and on the incident', async () => {
  on('GET', '/api/v1/alerts', () => json(200, { total: 1, limit: 50, offset: 0, alerts: [
    alertSummary(42, { delivered_to: 0, repeat_count: 3, last_repeat_at: '2026-10-22T10:30:00+00:00' }),
  ] }));
  await signedIn();
  await openAlertsTab();
  expect(await screen.findByText('Reached nobody when it fired')).toBeTruthy();
  expect(screen.getByText(/^Still firing — repeated 3× since, last /)).toBeTruthy();
  const row = screen.getByRole('link', { name: /^CRITICAL alert\. Withdrawal #42 to a new address\. Incident 42\./ });
  expect(row.props.accessibilityLabel).toMatch(/Still firing — repeated 3× since.*Not acknowledged\. Reached nobody when it fired$/);
  // The incident says it too.
  on('GET', '/api/v1/alerts/{id}', () => json(200, { ...alertSummary(42, { delivered_to: 0, repeat_count: 3,
    last_repeat_at: '2026-10-22T10:30:00+00:00' }), exchange: 'binance', evidence: {}, advice: null,
    balances_before: [], balances_after: [], expected_marks: [] }));
  await fireEvent.press(row);
  expect(await screen.findByText(/^Repeated 3× since, last /)).toBeTruthy();
  expect(screen.getByText('Not delivered')).toBeTruthy();
});

/** A server with `count` alerts, newest (highest id) first, paging as the API does. */
function manyAlerts(count: number) {
  const ids = Array.from({ length: count }, (_, i) => count - i);
  const held: (() => void)[] = [];
  let holding = false;
  on('GET', '/api/v1/alerts', (request) => {
    const query = new URL(request.url).searchParams;
    const limit = Number(query.get('limit') ?? 50);
    const offset = Number(query.get('offset') ?? 0);
    const answer = () => json(200, { total: ids.length, limit, offset, alerts: ids.slice(offset, offset + limit).map((id) =>
      alertSummary(id, demo.acknowledged.has(id)
        ? { acknowledged_at: '2026-10-22T10:05:00+00:00', acknowledged_by: 'demo@minas.example' } : {})) });
    if (!holding) return answer();
    return new Promise<Response>((resolve) => held.push(() => resolve(answer())));
  });
  return {
    arrive: () => ids.unshift(ids.length + 1),
    /** Hold every answer until released, in the order given. */
    hold: () => {
      holding = true;
    },
    release: (order: number[]) => order.map((i) => held[i]).forEach((go) => go()),
  };
}

const alertQueries = () => sent.filter((r) => r.call === 'GET /api/v1/alerts').map((r) => r.query);

test('older alerts load a page at a time until there are none left', async () => {
  manyAlerts(120);
  await signedIn();
  await openAlertsTab();
  expect(await screen.findByText('120 alert(s) on your accounts, newest first')).toBeTruthy();
  expect(screen.getAllByText(ROW)).toHaveLength(50);
  await fireEvent.press(screen.getByRole('button', { name: 'Show older alerts' }));
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(100));
  await fireEvent.press(screen.getByRole('button', { name: 'Show older alerts' }));
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(120));
  expect(screen.queryByRole('button', { name: 'Show older alerts' })).toBeNull();
  // Each time, everything asked for, fresh from the top.
  expect(alertQueries()).toEqual(['limit=50', 'limit=100', 'limit=150']);
});

test('an alert arriving between pages shows at the top, and every alert once', async () => {
  const errors = jest.spyOn(console, 'error');
  const server = manyAlerts(60);
  await signedIn();
  await openAlertsTab();
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(50));
  server.arrive(); // alert 61, on top
  await fireEvent.press(screen.getByRole('button', { name: 'Show older alerts' }));
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(61));
  const shown = screen.getAllByText(ROW).map((node) => node.props.children);
  expect(shown[0]).toBe('Withdrawal #61 to a new address');
  expect(new Set(shown).size).toBe(shown.length); // no alert twice
  expect(errors.mock.calls.flat().join(' ')).not.toMatch(/same key/);
  errors.mockRestore();
});

test('past 200 rows, coming back from an incident keeps every row, the one handled included', async () => {
  manyAlerts(260);
  await signedIn();
  await openAlertsTab();
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(50));
  for (const rows of [100, 150, 200, 250, 260]) {
    await fireEvent.press(screen.getByRole('button', { name: 'Show older alerts' }));
    await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(rows));
  }
  await fireEvent.press(screen.getByText('Withdrawal #3 to a new address'));
  await fireEvent.press(await screen.findByRole('button', { name: 'Acknowledge alert' }));
  await screen.findByText(/^By demo@minas\.example at/);
  await testRouter.back('/alerts');
  await waitFor(() => expect(screen.getByText('Acknowledged by demo@minas.example')).toBeTruthy());
  expect(screen.getAllByText(ROW)).toHaveLength(260);
  // Read in two, as the API serves at most 200 at a time.
  expect(alertQueries().slice(-2)).toEqual(['limit=200', 'limit=100&offset=200']);
});

test.each([
  ['the reload answers first', [1, 0]],
  ['the older page answers first', [0, 1]],
])('a reload that overlaps "Show older alerts" loses neither (%s)', async (_order, order) => {
  const server = manyAlerts(120);
  await signedIn();
  await openAlertsTab();
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(50));
  server.hold();
  await act(async () => {
    void fireEvent.press(screen.getByRole('button', { name: 'Show older alerts' }));
  });
  await pullToRefresh();
  await act(async () => server.release(order));
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(100));
  expect(screen.getByRole('button', { name: 'Show older alerts' })).toBeTruthy();
});

test('an acknowledgement Minas does not answer in time is not reported as refused', async () => {
  on('POST', '/api/v1/alerts/{id}/acknowledge', silence);
  await openSignedIn('/alert/42');
  const button = await screen.findByRole('button', { name: 'Acknowledge alert' });
  // The tap settles only when the acknowledgement does, which takes the
  // 45-second deadline: tap, and let the clock run, inside one act.
  await act(async () => {
    const tapped = onPressOf(button)();
    await jest.advanceTimersByTimeAsync(45_000);
    await tapped;
  });
  expect(await screen.findByText(
    "Minas didn't answer in time. The acknowledgement may still have been recorded: pull down to check.")).toBeTruthy();
});

test('coming back to the list reads everything already on it, in one call', async () => {
  manyAlerts(120);
  await signedIn();
  await openAlertsTab();
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(50));
  await fireEvent.press(screen.getByRole('button', { name: 'Show older alerts' }));
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(100));
  await fireEvent.press(screen.getByText('Withdrawal #77 to a new address'));
  await waitFor(() => expect(headerTitles()).toContain('Incident #77'));
  await testRouter.back('/alerts');
  await waitFor(() => expect(alertQueries().at(-1)).toBe('limit=100'));
  expect(screen.getAllByText(ROW)).toHaveLength(100);
});

test('an acknowledgement Minas answers slowly, inside the write deadline, still lands', async () => {
  on('POST', '/api/v1/alerts/{id}/acknowledge', (_r, [id]) => new Promise<Response>((resolve) => setTimeout(() => {
    demo.acknowledged.add(Number(id));
    resolve(json(200, { id: Number(id), newly_acknowledged: true, acknowledged_at: '2026-10-22T10:05:00+00:00',
      acknowledged_by: 'demo@minas.example' }));
  }, 20_000)));
  await openSignedIn('/alert/42');
  const button = await screen.findByRole('button', { name: 'Acknowledge alert' });
  await act(async () => {
    const tapped = onPressOf(button)();
    await jest.advanceTimersByTimeAsync(20_000); // past a read's 15 s, inside a write's 45
    await tapped;
  });
  expect(await screen.findByText(/^By demo@minas\.example at/)).toBeTruthy();
});

test('past 200 rows, a reload whose second read fails keeps the whole list it had, never part of a new one', async () => {
  manyAlerts(260);
  await signedIn();
  await openAlertsTab();
  await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(50));
  for (const rows of [100, 150, 200, 250, 260]) {
    await fireEvent.press(screen.getByRole('button', { name: 'Show older alerts' }));
    await waitFor(() => expect(screen.getAllByText(ROW)).toHaveLength(rows));
  }
  const all = [...Array(260)].map((_, i) => 260 - i);
  on('GET', '/api/v1/alerts', (request) => {
    const query = new URL(request.url).searchParams;
    if (query.get('offset') === '200') return json(500);
    const limit = Number(query.get('limit'));
    return json(200, { total: 260, limit, offset: 0, alerts: all.slice(0, limit).map((id) => alertSummary(id)) });
  });
  await pullToRefresh();
  expect(await screen.findByText('Showing what we had last.')).toBeTruthy();
  expect(screen.getAllByText(ROW)).toHaveLength(260);
});
