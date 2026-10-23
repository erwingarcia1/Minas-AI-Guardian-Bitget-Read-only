/// <reference types="jest" />
// The whole app, rendered with its real routes (expo-router's renderRouter)
// against the fake server, with the answers a demo login would get.
import { userEvent } from '@testing-library/react-native';
import { act, configure, isHiddenFromAccessibility, renderRouter, screen } from 'expo-router/testing-library';

import { json, on, REFRESH_KEY, resetServer, store } from './server';

// renderRouter runs on Jest's fake timers; the app's chain (secure store,
// refresh, request, render) takes more simulated time than the 1s default.
configure({ asyncUtilTimeout: 5000 });

export const SIGN_IN_LINE = 'Sign in with your member portal email, password and two-factor code.';
export const REFUSED_REASON = 'our monitoring key is being rejected — replace it and monitoring restarts';

export function tokens(n: number) {
  return {
    token_type: 'Bearer',
    access_token: `access-${n}`,
    expires_in: 900,
    refresh_token: `refresh-${n}`,
    refresh_expires_in: 2592000,
    device_id: 7,
  };
}

export const ACCOUNTS = {
  overall: 'degraded',
  checked_at: '2026-10-22T10:00:00+00:00',
  stale_after_seconds: 225,
  accounts: [
    { id: 1, name: 'Binance - savings', exchange: 'binance', status: 'active', watched: true, reason: null,
      last_success_at: '2026-10-22T10:00:00+00:00', role: 'primary' },
    { id: 2, name: 'Bitget - trading', exchange: 'bitget', status: 'active', watched: false, reason: REFUSED_REASON,
      last_success_at: '2026-10-22T07:00:00+00:00', role: 'primary' },
  ],
};

export function alertSummary(id: number, extra: object = {}) {
  return { id, account_id: 1, account_name: 'Binance - savings', sent_at: '2026-10-22T09:48:00+00:00',
    severity: 'CRITICAL', rule: 'withdrawal_to_unseen_address', message: `Withdrawal #${id} to a new address`,
    confidence: 'high', delivered_to: 2, acknowledged_at: null, acknowledged_by: null, repeat_count: null,
    last_repeat_at: null, ...extra };
}

export const SECURITY = {
  two_factor_enabled: true,
  recovery_codes_left: 8,
  devices: [
    { id: 7, name: 'Test phone', signed_in_at: '2026-10-22T09:00:00+00:00', last_refreshed_at: null, current: true },
    { id: 9, name: 'Old tablet', signed_in_at: '2026-10-01T09:00:00+00:00', last_refreshed_at: null, current: false },
  ],
};

export const LIMITS = {
  headline: { title: 'Minas AI Guardian cannot stop an attack.', text: 'It watches and tells you.' },
  does: [], cannot: [], timing: { balances_every_seconds: 45, permissions_every_seconds: 600 }, retention: [],
  retention_reviewed: false, sensitivity: [],
};

/** The server's state a test can look at or change. */
export const server = { acknowledged: new Set<number>() };

function incident(id: number) {
  const acknowledged = server.acknowledged.has(id);
  return { ...alertSummary(id), exchange: 'binance', evidence: { coin: 'BTC', amount: '0.85' },
    acknowledged_at: acknowledged ? '2026-10-22T10:05:00+00:00' : null,
    acknowledged_by: acknowledged ? 'demo@minas.example' : null,
    advice: { lead: 'Act now.', steps: ['Revoke any API key you do not recognise.'], note: '' },
    balances_before: [{ coin: 'BTC', quantity: '1.20000000', snapshot_at: '2026-10-22T09:47:00+00:00' }],
    balances_after: [{ coin: 'BTC', quantity: '0.35000000', snapshot_at: '2026-10-22T09:49:00+00:00' }],
    expected_marks: [] };
}

/** An alert as the list shows it, acknowledged if the server says so. */
function summary(id: number) {
  const { acknowledged_at, acknowledged_by } = incident(id);
  return alertSummary(id, { acknowledged_at, acknowledged_by });
}

/** A demo login's server: one critical alert, two accounts (one in
 * trouble), two phones. */
export function defaultServer() {
  server.acknowledged.clear();
  let issued = 1;
  on('POST', '/api/v1/session', () => json(200, tokens(1)));
  on('POST', '/api/v1/session/refresh', () => json(200, tokens(++issued)));
  on('DELETE', '/api/v1/session', () => json(204));
  on('GET', '/api/v1/accounts', () => json(200, ACCOUNTS));
  on('GET', '/api/v1/alerts', () => json(200, { total: 1, limit: 50, offset: 0, alerts: [summary(42)] }));
  on('GET', '/api/v1/alerts/{id}', (_r, [id]) => json(200, incident(Number(id))));
  on('POST', '/api/v1/alerts/{id}/acknowledge', (_r, [id]) => {
    server.acknowledged.add(Number(id));
    const { acknowledged_at, acknowledged_by } = incident(Number(id));
    return json(200, { id: Number(id), newly_acknowledged: true, acknowledged_at, acknowledged_by });
  });
  on('GET', '/api/v1/security', () => json(200, SECURITY));
  on('DELETE', '/api/v1/devices/{id}', () => json(204));
  on('GET', '/api/v1/activity', () => json(200, { events: [{ id: 1, account_id: 1, account_name: 'Binance - savings',
    type: 'member_app_signed_in', title: 'Phone app signed in', detail: 'login demo@minas.example · Test phone',
    actor: 'demo@minas.example', created_at: '2026-10-22T09:00:00+00:00' }] }));
  on('GET', '/api/v1/limits', () => json(200, LIMITS));
}

/** For a test file: a fresh fake server with the demo answers before each
 * test, and the app's last effects settled after it. */
export function useDemoServer() {
  beforeEach(() => {
    resetServer();
    defaultServer();
  });
  afterEach(async () => {
    await act(async () => {});
  });
}

/** Open the app on a phone that is already signed in. */
export async function openSignedIn(initialUrl = '/') {
  store.set(REFRESH_KEY, 'refresh-1');
  const app = await renderRouter('./src/app', { initialUrl });
  return app;
}

/** Open the app signed in, and wait for the accounts. */
export async function signedIn() {
  const app = await openSignedIn();
  await screen.findByText('Some of your accounts are not being watched.');
  return app;
}

/** Pull down on the screen in view, as a member does to refresh it. */
export async function pullToRefresh() {
  const scrollers = screen.container.queryAll(
    (node) => node.type === 'RCTScrollView' && !!node.props.refreshControl && !isHiddenFromAccessibility(node),
  );
  if (scrollers.length !== 1) throw new Error(`expected one refreshable screen in view, found ${scrollers.length}`);
  await userEvent.setup().pullToRefresh(scrollers[0]);
}
