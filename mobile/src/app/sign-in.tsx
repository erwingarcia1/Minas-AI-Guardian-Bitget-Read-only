import { type ComponentRef, type Ref, useRef, useState } from 'react';
import {
  Image,
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useSession } from '@/auth/session';
import { API_URL } from '@/config';
import { colors, radius, space, type } from '@/theme';
import { Body, Button, Notice } from '@/ui';

type Input = ComponentRef<typeof TextInput>;

// The app's sign-in is the portal's, with two-factor required (decision 21c).
// The server answers a wrong password and a wrong code alike, on purpose, and
// its messages are written for the member - they are shown as they come.
export default function SignIn() {
  const { signIn, ended } = useSession();
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
    setProblem({
      title: result.code === 'two_factor_required' ? 'Two-factor sign-in is needed first.' : 'Not signed in.',
      message: result.message,
    });
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

          {ended && !problem ? (
            <Notice tone="warn" title="This phone was signed out.">
              That happens when the app goes 30 days unopened, when this phone is signed out from the portal&apos;s
              Security page or another phone, or after a password change. If none of those was you, change your
              password on the member portal.
            </Notice>
          ) : null}
          {problem ? (
            <Notice tone="danger" title={problem.title}>
              {problem.message}
            </Notice>
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

          <Body muted style={styles.phish}>
            Minas will never email you a link asking for your password or API key. Sign in only here, or on the portal
            address you were given.
          </Body>

          {__DEV__ ? <Body muted style={styles.phish}>{`Development build. Server: ${API_URL}`}</Body> : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Field({
  label,
  hint,
  ref,
  ...input
}: { label: string; hint?: string; ref?: Ref<Input> } & Pick<
  React.ComponentProps<typeof TextInput>,
  | 'value'
  | 'onChangeText'
  | 'autoComplete'
  | 'keyboardType'
  | 'secureTextEntry'
  | 'returnKeyType'
  | 'submitBehavior'
  | 'onSubmitEditing'
>) {
  return (
    <View style={{ gap: space(1) }}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        {...input}
        ref={ref}
        accessibilityLabel={label}
        accessibilityHint={hint}
        autoCapitalize="none"
        autoCorrect={false}
        placeholderTextColor={colors.textSubtle}
        style={styles.input}
      />
      {hint ? <Text style={styles.hint}>{hint}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  body: { flexGrow: 1, justifyContent: 'center', padding: space(5), gap: space(4) },
  brand: { alignItems: 'center', gap: space(2), marginBottom: space(4) },
  mark: { width: 96, height: 96 },
  name: { color: colors.text, fontSize: type.xl, fontWeight: '700' },
  label: { color: colors.textMuted, fontSize: type.sm },
  hint: { color: colors.textMuted, fontSize: type.sm },
  input: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    color: colors.text,
    fontSize: type.base,
    paddingHorizontal: space(3),
  },
  link: { color: colors.gold, fontSize: type.sm, textAlign: 'center' },
  phish: { fontSize: type.sm, textAlign: 'center' },
});
