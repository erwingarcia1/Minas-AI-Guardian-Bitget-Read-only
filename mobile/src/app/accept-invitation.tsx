import * as Device from 'expo-device';
import { useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { describeError, publicApi } from '@/api/client';
import { isTimeout } from '@/api/network';
import { useSession } from '@/auth/session';
import { RecoveryCodes, TwoFactorSetup } from '@/auth/two-factor';
import { colors, space } from '@/theme';
import { Body, Button, Notice, Title } from '@/ui';
import { Field, type Input } from '@/ui/field';

// The portal's /welcome, on the phone (decision 17, 2026-10-28): Minas emails
// a member an invitation code when it sets their monitoring up, and the
// member makes their own login from it - the code, a password, then
// two-factor, and only then does the login exist. The server's sentences are
// the portal's (sentinel/invitations.py and sentinel/portal.py), shown as
// they come; the fixed ones here are pinned to the portal's by
// tests/test_mobile_app.py.
const TITLE = 'Accept your invitation';
const LEAD = 'Minas emailed you an invitation code when your monitoring was set up. It works once.';
const CODE_HINT = 'Like 7KQM-3XRP-9WTD. Never share it: Minas will never ask you for it.';
const PASSWORD_HINT = 'At least 12 characters.';

type Step =
  | { name: 'code' }
  | { name: 'password'; token: string; email: string }
  | { name: 'two-factor'; token: string; secret: string; uri: string }
  | { name: 'codes'; codes: string[]; answer: unknown };

type Problem = { title: string; message: string };

export default function AcceptInvitation() {
  const { adopt } = useSession();
  const [step, setStep] = useState<Step>({ name: 'code' });
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const codeField = useRef<Input>(null);

  /** One step's call: the answer, or null with the problem shown. A step
   * whose proof has expired goes back to the first. */
  async function call<T>(send: () => Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T | null> {
    setBusy(true);
    setProblem(null);
    try {
      const { data, error, response } = await send();
      if (data) return data;
      const reason = (error as { error?: string } | undefined)?.error;
      if (reason === 'step_expired') setStep({ name: 'code' });
      setProblem({ title: 'Not done yet.', message: describeError(error, response) });
      return null;
    } catch (thrown) {
      setProblem({ title: 'Not done yet.', message: describeError(undefined, undefined, thrown) });
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function checkCode() {
    const answer = await call(() =>
      publicApi.POST('/api/v1/invitation/code', { body: { email: email.trim(), code: code.trim() } }),
    );
    if (answer) setStep({ name: 'password', token: answer.step_token, email: answer.email });
  }

  async function choosePassword(token: string) {
    const answer = await call(() =>
      publicApi.POST('/api/v1/invitation/password', { body: { step_token: token, password } }),
    );
    if (answer) {
      setPassword('');
      setStep({ name: 'two-factor', token: answer.step_token, secret: answer.secret, uri: answer.otpauth_uri });
    }
  }

  async function finish(token: string, totp: string) {
    const deviceName = Device.deviceName ?? Device.modelName ?? undefined;
    setBusy(true);
    setProblem(null);
    try {
      const { data, error, response } = await publicApi.POST('/api/v1/invitation/two-factor', {
        body: { step_token: token, code: totp, device_name: deviceName },
      });
      if (data) {
        setStep({ name: 'codes', codes: data.recovery_codes, answer: data });
        return;
      }
      const reason = (error as { error?: string } | undefined)?.error;
      if (reason === 'step_expired') setStep({ name: 'code' });
      setProblem({ title: 'Not done yet.', message: describeError(error, response) });
    } catch (thrown) {
      setProblem(
        isTimeout(thrown)
          ? {
              title: 'Not sure it went through.',
              // The login may have been made, and the code spent, before the
              // answer was lost.
              message:
                "Minas didn't answer in time. Your login may already have been made: try signing in, with the next code from your authenticator app.",
            }
          : { title: 'Not done yet.', message: describeError(undefined, undefined, thrown) },
      );
    } finally {
      setBusy(false);
    }
  }

  async function done(answer: unknown) {
    if (!(await adopt(answer))) {
      setProblem({ title: 'Not signed in.', message: describeError(undefined, undefined, new SyntaxError()) });
    }
    // Signed in: the layout moves on to the accounts by itself.
  }

  return (
    <SafeAreaView style={styles.screen} edges={['left', 'right', 'bottom']}>
      <KeyboardAvoidingView behavior="padding" style={styles.screen}>
        <ScrollView
          contentContainerStyle={styles.body}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode={Platform.OS === 'ios' ? 'interactive' : 'on-drag'}>
          {problem ? (
            <Notice tone="danger" title={problem.title}>
              {problem.message}
            </Notice>
          ) : null}

          {step.name === 'code' ? (
            <View style={styles.group}>
              <Title>{TITLE}</Title>
              <Body muted>{LEAD}</Body>
              <Field
                label="Email address"
                value={email}
                onChangeText={setEmail}
                autoComplete="email"
                keyboardType="email-address"
                returnKeyType="next"
                submitBehavior="submit"
                onSubmitEditing={() => codeField.current?.focus()}
              />
              <Field
                ref={codeField}
                label="Invitation code"
                hint={CODE_HINT}
                value={code}
                onChangeText={setCode}
                autoComplete="one-time-code"
                autoCapitalize="characters"
                returnKeyType="go"
                onSubmitEditing={checkCode}
              />
              <Button label="Continue" busy={busy} disabled={!email.trim() || !code.trim()} onPress={checkCode} />
            </View>
          ) : null}

          {step.name === 'password' ? (
            <View style={styles.group}>
              <Title>Choose a password</Title>
              <Body muted>{`For ${step.email}. You will also set up two-factor sign-in next, so have your authenticator app ready.`}</Body>
              <Field
                label="Password"
                hint={PASSWORD_HINT}
                value={password}
                onChangeText={setPassword}
                autoComplete="new-password"
                secureTextEntry
                returnKeyType="go"
                onSubmitEditing={() => choosePassword(step.token)}
              />
              <Button label="Continue" busy={busy} disabled={!password} onPress={() => choosePassword(step.token)} />
            </View>
          ) : null}

          {step.name === 'two-factor' ? (
            <TwoFactorSetup
              secret={step.secret}
              uri={step.uri}
              confirmLabel="Create my login"
              busy={busy}
              onConfirm={(totp) => finish(step.token, totp)}
            />
          ) : null}

          {step.name === 'codes' ? <RecoveryCodes codes={step.codes} onDone={() => done(step.answer)} /> : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  body: { flexGrow: 1, padding: space(5), gap: space(4) },
  group: { gap: space(3) },
});
