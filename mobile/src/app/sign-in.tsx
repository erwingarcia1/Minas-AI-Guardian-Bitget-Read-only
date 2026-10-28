import * as Device from 'expo-device';
import { useRouter } from 'expo-router';
import { useRef, useState } from 'react';
import { Image, KeyboardAvoidingView, Linking, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { describeError, publicApi } from '@/api/client';
import { useSession } from '@/auth/session';
import { RecoveryCodes, TwoFactorSetup } from '@/auth/two-factor';
import { API_URL } from '@/config';
import { colors, space, type } from '@/theme';
import { Body, Button, Notice } from '@/ui';
import { Field, type Input } from '@/ui/field';

// Two-factor set up on this phone, for a login that has a password and no
// second factor yet (decision 17, 2026-10-28): the dead end 21c left, where
// the only place to turn it on was the portal's Security page.
type Setup = { name: 'none' } | { name: 'two-factor'; secret: string; uri: string } | { name: 'codes'; codes: string[]; answer: unknown };

// The app's sign-in is the portal's, with two-factor required (decision 21c).
// The server answers a wrong password and a wrong code alike, on purpose, and
// its messages are written for the member - they are shown as they come.
export default function SignIn() {
  const { signIn, adopt } = useSession();
  const router = useRouter();
  const [setup, setSetup] = useState<Setup>({ name: 'none' });
  const [needsTwoFactor, setNeedsTwoFactor] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ title: string; message: string } | null>(null);
  const passwordField = useRef<Input>(null);
  const codeField = useRef<Input>(null);
  const ready = !!email && !!password && !!code && !busy;

  async function submit() {
    if (!ready) return;
    setBusy(true);
    setProblem(null);
    const result = await signIn(email, password, code);
    setBusy(false);
    if (result.ok) return; // the router moves on by itself
    setNeedsTwoFactor(result.code === 'two_factor_required');
    setProblem({
      title: result.code === 'two_factor_required' ? 'Two-factor sign-in is needed first.' : 'Not signed in.',
      message: result.message,
    });
  }

  async function startTwoFactor() {
    setBusy(true);
    setProblem(null);
    try {
      const { data, error, response } = await publicApi.POST('/api/v1/two-factor/start', {
        body: { email: email.trim(), password },
      });
      if (data) setSetup({ name: 'two-factor', secret: data.secret, uri: data.otpauth_uri });
      else setProblem({ title: 'Two-factor is not on yet.', message: describeError(error, response) });
    } catch (thrown) {
      setProblem({ title: 'Two-factor is not on yet.', message: describeError(undefined, undefined, thrown) });
    } finally {
      setBusy(false);
    }
  }

  async function confirmTwoFactor(totp: string) {
    const deviceName = Device.deviceName ?? Device.modelName ?? undefined;
    setBusy(true);
    setProblem(null);
    try {
      const { data, error, response } = await publicApi.POST('/api/v1/two-factor/confirm', {
        body: { email: email.trim(), password, code: totp, device_name: deviceName },
      });
      if (data) setSetup({ name: 'codes', codes: data.recovery_codes, answer: data });
      else setProblem({ title: 'Two-factor is not on yet.', message: describeError(error, response) });
    } catch (thrown) {
      setProblem({ title: 'Two-factor is not on yet.', message: describeError(undefined, undefined, thrown) });
    } finally {
      setBusy(false);
    }
  }

  async function signedInWithCodes(answer: unknown) {
    if (!(await adopt(answer))) {
      setProblem({ title: 'Not signed in.', message: describeError(undefined, undefined, new SyntaxError()) });
    }
  }

  if (setup.name !== 'none') {
    return (
      <SafeAreaView style={styles.screen}>
        <KeyboardAvoidingView behavior="padding" style={styles.screen}>
          <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
            {problem ? (
              <Notice tone="danger" title={problem.title}>
                {problem.message}
              </Notice>
            ) : null}
            {setup.name === 'two-factor' ? (
              <TwoFactorSetup
                secret={setup.secret}
                uri={setup.uri}
                confirmLabel="Turn on two-factor"
                busy={busy}
                onConfirm={confirmTwoFactor}
              />
            ) : (
              <RecoveryCodes codes={setup.codes} onDone={() => signedInWithCodes(setup.answer)} />
            )}
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.screen}>
      {/* The form scrolls, so the button can always be reached: above the
          keyboard, and at the largest text sizes. */}
      <KeyboardAvoidingView behavior="padding" style={styles.screen}>
        <ScrollView
          contentContainerStyle={styles.body}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode={Platform.OS === 'ios' ? 'interactive' : 'on-drag'}>
          <View style={styles.brand}>
            <Image
              source={require('../../assets/images/splash-icon.png')}
              style={styles.mark}
              accessibilityIgnoresInvertColors
            />
            <Text style={styles.name} maxFontSizeMultiplier={1.5}>
              Minas AI <Text style={{ color: colors.gold }}>Guardian</Text>
            </Text>
            <Body muted>Sign in with your member portal email, password and two-factor code.</Body>
          </View>

          {problem ? (
            <Notice tone="danger" title={problem.title}>
              {problem.message}
            </Notice>
          ) : null}
          {needsTwoFactor ? (
            <Button label="Set up two-factor here" variant="secondary" busy={busy} onPress={startTwoFactor} />
          ) : null}

          <Field
            label="Email"
            value={email}
            onChangeText={setEmail}
            autoComplete="email"
            keyboardType="email-address"
            returnKeyType="next"
            submitBehavior="submit"
            onSubmitEditing={() => passwordField.current?.focus()}
          />
          <Field
            ref={passwordField}
            label="Password"
            value={password}
            onChangeText={setPassword}
            autoComplete="current-password"
            secureTextEntry
            returnKeyType="next"
            submitBehavior="submit"
            onSubmitEditing={() => codeField.current?.focus()}
          />
          <Field
            ref={codeField}
            label="Code from your authenticator app"
            hint="Or one of your recovery codes."
            value={code}
            onChangeText={setCode}
            autoComplete="one-time-code"
            returnKeyType="go"
            onSubmitEditing={submit}
          />

          <Button label="Sign in" onPress={submit} busy={busy} disabled={!email || !password || !code} />

          <Pressable accessibilityRole="link" onPress={() => Linking.openURL(`${API_URL}/forgot`)} hitSlop={8}>
            <Text style={styles.link}>Forgot password? Reset it on the member portal rather than retrying.</Text>
          </Pressable>

          {/* The portal's sign-in page says the same (decision 17): a member
              with no login yet makes one from Minas's emailed code. */}
          <Pressable accessibilityRole="link" onPress={() => router.push('/accept-invitation')} hitSlop={8}>
            <Text style={styles.link}>First time here? Accept an invitation with the code Minas emailed you.</Text>
          </Pressable>

          <Body muted style={styles.phish}>
            Minas will never email you a link asking for your password or API key. Sign in only here, or on the portal
            address you were given.
          </Body>

          {/* The portal's sign-in page carries the same link (decision 27). */}
          <Pressable accessibilityRole="link" onPress={() => Linking.openURL(`${API_URL}/privacy`)} hitSlop={8}>
            <Text style={styles.link}>Privacy policy</Text>
          </Pressable>

          {__DEV__ ? <Body muted style={styles.phish}>{`Development build. Server: ${API_URL}`}</Body> : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  body: { flexGrow: 1, justifyContent: 'center', padding: space(5), gap: space(4) },
  brand: { alignItems: 'center', gap: space(2), marginBottom: space(4) },
  mark: { width: 96, height: 96 },
  name: { color: colors.text, fontSize: type.xl, fontWeight: '700' },
  link: { color: colors.gold, fontSize: type.sm, textAlign: 'center' },
  phish: { fontSize: type.sm, textAlign: 'center' },
});
