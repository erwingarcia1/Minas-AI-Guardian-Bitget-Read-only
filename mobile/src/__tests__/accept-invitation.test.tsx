/// <reference types="jest" />
/**
 * Accepting an invitation, and setting up two-factor before a first sign-in
 * (decision 17, 2026-10-28), with the app's real routes against the fake
 * server. The server's own rules are tested in tests/test_api_invitations.py.
 */
import { fireEvent, renderRouter, screen } from 'expo-router/testing-library';
import { Linking } from 'react-native';

import { tokens, useDemoServer } from '@/test-utils/app';
import { bodies, calls, json, on, REFRESH_KEY, store } from '@/test-utils/server';

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(async () => true),
  hideAsync: jest.fn(async () => undefined),
}));
jest.setTimeout(30_000);

useDemoServer();

const SECRET = 'JBSWY3DPEHPK3PXP';
const URI = `otpauth://totp/Minas%20AI%20Guardian:carol%40example.com?secret=${SECRET}&issuer=Minas%20AI%20Guardian`;
const CODES = ['aaaa-bbbb', 'cccc-dddd'];

function invitationServer() {
  on('POST', '/api/v1/invitation/code', () => json(200, { step_token: 'step-1', email: 'carol@example.com' }));
  on('POST', '/api/v1/invitation/password', () =>
    json(200, { step_token: 'step-2', secret: SECRET, otpauth_uri: URI }));
  on('POST', '/api/v1/invitation/two-factor', () => json(200, { ...tokens(1), recovery_codes: CODES }));
}

async function openInvitation() {
  return renderRouter('./src/app', { initialUrl: '/accept-invitation' });
}

async function throughTheCode() {
  await fireEvent.changeText(await screen.findByLabelText('Email address'), ' carol@example.com ');
  await fireEvent.changeText(screen.getByLabelText('Invitation code'), ' 7kqm-3xrp-9wtd ');
  await fireEvent.press(screen.getByRole('button', { name: 'Continue' }));
}

test('the whole flow: code, password, two-factor, the recovery codes, then the accounts', async () => {
  invitationServer();
  const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
  const app = await openInvitation();
  expect(await screen.findByText('Accept your invitation')).toBeTruthy();

  await throughTheCode();
  expect(bodies['POST /api/v1/invitation/code']).toEqual({ email: 'carol@example.com', code: '7kqm-3xrp-9wtd' });
  expect(await screen.findByText('Choose a password')).toBeTruthy();
  await fireEvent.changeText(screen.getByLabelText('Password'), 'a-long-enough-password');
  await fireEvent.press(screen.getByRole('button', { name: 'Continue' }));
  expect(bodies['POST /api/v1/invitation/password']).toEqual({ step_token: 'step-1',
    password: 'a-long-enough-password' });

  // A phone cannot scan its own screen: the link opens the authenticator.
  expect(await screen.findByText(SECRET)).toBeTruthy();
  await fireEvent.press(screen.getByRole('button', { name: 'Open in your authenticator app' }));
  expect(open).toHaveBeenCalledWith(URI);
  await fireEvent.changeText(screen.getByLabelText('Code from your authenticator app'), ' 123456 ');
  await fireEvent.press(screen.getByRole('button', { name: 'Create my login' }));
  expect(bodies['POST /api/v1/invitation/two-factor']).toEqual({ step_token: 'step-2', code: '123456',
    device_name: 'Test phone' });

  // The codes come first, and nothing is stored until they have been seen.
  expect(await screen.findByText(CODES.join('\n'))).toBeTruthy();
  expect(store.has(REFRESH_KEY)).toBe(false);
  await fireEvent.press(screen.getByRole('button', { name: 'I have saved them' }));
  expect(await screen.findByText('Some of your accounts are not being watched.')).toBeTruthy();
  expect(app.getPathname()).toBe('/');
  expect(store.get(REFRESH_KEY)).toBe('refresh-1');
  open.mockRestore();
});

test("a refused code shows the server's own sentence and stays on the first step", async () => {
  on('POST', '/api/v1/invitation/code', () =>
    json(400, { error: 'invitation_not_accepted', message: "That email address and code don't match an open invitation." }));
  await openInvitation();
  await throughTheCode();
  expect(await screen.findByText("That email address and code don't match an open invitation.")).toBeTruthy();
  expect(screen.getByLabelText('Invitation code')).toBeTruthy();
  expect(calls).not.toContain('POST /api/v1/invitation/password');
});

test('a step whose proof expired goes back to the first step, saying so', async () => {
  invitationServer();
  on('POST', '/api/v1/invitation/password', () =>
    json(400, { error: 'step_expired', message: 'That step took too long. Enter your email address and code again.' }));
  await openInvitation();
  await throughTheCode();
  await fireEvent.changeText(await screen.findByLabelText('Password'), 'a-long-enough-password');
  await fireEvent.press(screen.getByRole('button', { name: 'Continue' }));
  expect(await screen.findByText('That step took too long. Enter your email address and code again.')).toBeTruthy();
  expect(screen.getByLabelText('Invitation code')).toBeTruthy();
});

test('the welcome screen leads here', async () => {
  const app = await renderRouter('./src/app', { initialUrl: '/landing' });
  await fireEvent.press(await screen.findByRole('button', { name: 'Accept an invitation' }));
  expect(await screen.findByLabelText('Invitation code')).toBeTruthy();
  expect(app.getPathname()).toBe('/accept-invitation');
});

test('a login without two-factor sets it up from the sign-in screen and is signed in', async () => {
  on('POST', '/api/v1/session', () => json(403, { error: 'two_factor_required',
    message: "Two-factor sign-in is not on for this login yet. Set it up here, or on the portal's Security page." }));
  on('POST', '/api/v1/two-factor/start', () => json(200, { secret: SECRET, otpauth_uri: URI }));
  on('POST', '/api/v1/two-factor/confirm', () => json(200, { ...tokens(1), recovery_codes: CODES }));
  const app = await renderRouter('./src/app', { initialUrl: '/sign-in' });
  await fireEvent.changeText(await screen.findByLabelText('Email'), 'bob@example.com');
  await fireEvent.changeText(screen.getByLabelText('Password'), 'a-long-enough-password');
  await fireEvent.changeText(screen.getByLabelText('Code from your authenticator app'), '000000');
  await fireEvent.press(screen.getByRole('button', { name: 'Sign in' }));

  await fireEvent.press(await screen.findByRole('button', { name: 'Set up two-factor here' }));
  expect(bodies['POST /api/v1/two-factor/start']).toEqual({ email: 'bob@example.com',
    password: 'a-long-enough-password' });
  expect(await screen.findByText(SECRET)).toBeTruthy();
  await fireEvent.changeText(screen.getByLabelText('Code from your authenticator app'), '654321');
  await fireEvent.press(screen.getByRole('button', { name: 'Turn on two-factor' }));
  expect(bodies['POST /api/v1/two-factor/confirm']).toEqual({ email: 'bob@example.com',
    password: 'a-long-enough-password', code: '654321', device_name: 'Test phone' });

  expect(await screen.findByText(CODES.join('\n'))).toBeTruthy();
  expect(store.has(REFRESH_KEY)).toBe(false);
  await fireEvent.press(screen.getByRole('button', { name: 'I have saved them' }));
  expect(await screen.findByText('Some of your accounts are not being watched.')).toBeTruthy();
  expect(app.getPathname()).toBe('/');
});

test('a refused sign-in for another reason offers no two-factor setup', async () => {
  on('POST', '/api/v1/session', () => json(401, { error: 'invalid_credentials',
    message: 'That email, password or code was not accepted.' }));
  await renderRouter('./src/app', { initialUrl: '/sign-in' });
  await fireEvent.changeText(await screen.findByLabelText('Email'), 'bob@example.com');
  await fireEvent.changeText(screen.getByLabelText('Password'), 'wrong');
  await fireEvent.changeText(screen.getByLabelText('Code from your authenticator app'), '000000');
  await fireEvent.press(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByText('That email, password or code was not accepted.')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Set up two-factor here' })).toBeNull();
});
