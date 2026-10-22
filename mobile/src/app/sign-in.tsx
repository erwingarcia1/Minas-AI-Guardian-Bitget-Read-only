import { useState } from 'react';
import { Image, KeyboardAvoidingView, Platform, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useSession } from '@/auth/session';
import { colors, radius, space, type } from '@/theme';
import { Body, Button, Notice } from '@/ui';

// The app's sign-in is the portal's, with two-factor required (decision 21c).
// The server answers a wrong password and a wrong code alike, on purpose, and
// its messages are written for the member - they are shown as they come.
export default function SignIn() {
  const { signIn } = useSession();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ title: string; message: string } | null>(null);

  async function submit() {
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
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.body}>
        <View style={styles.brand}>
          <Image source={require('../../assets/images/splash-icon.png')} style={styles.mark} accessibilityIgnoresInvertColors />
          <Text style={styles.name}>
            Minas AI <Text style={{ color: colors.gold }}>Guardian</Text>
          </Text>
          <Body muted>Sign in with your member portal email, password and two-factor code.</Body>
        </View>

        {problem ? <Notice tone="danger" title={problem.title}>{problem.message}</Notice> : null}

        <Field label="Email" value={email} onChangeText={setEmail} autoComplete="email" keyboardType="email-address" />
        <Field label="Password" value={password} onChangeText={setPassword} autoComplete="current-password" secureTextEntry />
        <Field
          label="Code from your authenticator app"
          value={code}
          onChangeText={setCode}
          autoComplete="one-time-code"
          keyboardType="number-pad"
        />

        <Button label="Sign in" onPress={submit} busy={busy} disabled={!email || !password || !code} />
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Field({
  label,
  ...input
}: { label: string } & Pick<
  React.ComponentProps<typeof TextInput>,
  'value' | 'onChangeText' | 'autoComplete' | 'keyboardType' | 'secureTextEntry'
>) {
  return (
    <View style={{ gap: space(1) }}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        {...input}
        accessibilityLabel={label}
        autoCapitalize="none"
        autoCorrect={false}
        placeholderTextColor={colors.textSubtle}
        style={styles.input}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  body: { flex: 1, justifyContent: 'center', padding: space(5), gap: space(4) },
  brand: { alignItems: 'center', gap: space(2), marginBottom: space(4) },
  mark: { width: 96, height: 96 },
  name: { color: colors.text, fontSize: type.xl, fontWeight: '700' },
  label: { color: colors.textMuted, fontSize: type.sm },
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
});
