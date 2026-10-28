// Setting up two-factor on the phone that has the authenticator app
// (decision 17, 2026-10-28; 21c), for accepting an invitation and for a
// member whose login has a password and no second factor yet.
//
// A phone cannot scan its own screen, so there is no QR code here: the
// otpauth:// link opens the authenticator app with the secret filled in,
// and the secret itself is shown, selectable, for an app that does not take
// links. Nothing is copied to the clipboard by the app.
import { useState } from 'react';
import { Linking, StyleSheet, Text, View } from 'react-native';

import { colors, space, type } from '@/theme';
import { Body, Button, Title } from '@/ui';
import { Field } from '@/ui/field';

// The portal's sentence beside its recovery codes, word for word
// (tests/test_mobile_app.py pins it).
const RECOVERY_NOTE =
  'Each code works once and lets you sign in if you lose your phone. They are stored hashed, so this is the only time they can be shown — write them down or put them in a password manager now.';

export function TwoFactorSetup({
  secret,
  uri,
  confirmLabel,
  busy,
  onConfirm,
}: {
  secret: string;
  uri: string;
  confirmLabel: string;
  busy: boolean;
  onConfirm: (code: string) => void;
}) {
  const [code, setCode] = useState('');
  return (
    <View style={styles.group}>
      <Title>Set up two-factor sign-in</Title>
      <Body muted>Add Minas AI Guardian to your authenticator app, such as Google Authenticator, then enter the code it shows.</Body>
      <Button label="Open in your authenticator app" variant="secondary" onPress={() => Linking.openURL(uri)} />
      <Body muted>Or type this secret into the app yourself:</Body>
      <Text selectable style={styles.secret} accessibilityLabel={`Secret: ${secret.split('').join(' ')}`}>
        {secret}
      </Text>
      <Field
        label="Code from your authenticator app"
        value={code}
        onChangeText={setCode}
        autoComplete="one-time-code"
        keyboardType="number-pad"
        returnKeyType="go"
        onSubmitEditing={() => code.trim() && onConfirm(code.trim())}
      />
      <Button label={confirmLabel} busy={busy} disabled={!code.trim()} onPress={() => onConfirm(code.trim())} />
    </View>
  );
}

/** The one time recovery codes exist outside their hashes: the portal's
 * words, and nothing moves on until the member says they have them. */
export function RecoveryCodes({ codes, onDone }: { codes: readonly string[]; onDone: () => void }) {
  return (
    <View style={styles.group}>
      <Title>Save your recovery codes</Title>
      <Body muted>{RECOVERY_NOTE}</Body>
      <Text selectable style={styles.codes}>
        {codes.join('\n')}
      </Text>
      <Button label="I have saved them" onPress={onDone} />
    </View>
  );
}

const styles = StyleSheet.create({
  group: { gap: space(3) },
  secret: { color: colors.text, fontSize: type.base, fontFamily: 'monospace', letterSpacing: 1 },
  codes: { color: colors.text, fontSize: type.base, fontFamily: 'monospace', lineHeight: type.base * 1.6 },
});
