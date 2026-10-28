import { useRouter } from 'expo-router';
import { Image, Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import { useSession } from '@/auth/session';
import { API_URL } from '@/config';
import { colors, space, type } from '@/theme';
import { Body, Button, Card, Eyebrow, Notice, Screen, Section, Title } from '@/ui';

// The portal's landing page, on a phone (decision 25): what the Guardian is,
// how it works, what it cannot do, and the two ways in - signing in, and
// accepting the invitation Minas emails a member (decision 17, 2026-10-28).
// Nothing here needs a sign-in, and nothing here asks the server for anything.
//
// The sentences are the portal's own (sentinel/portal.py: LANDING_STATEMENT,
// LANDING_LEAD, LANDING_SETUP, LANDING_STEPS, LIMITS_HEADLINE, LIMITS_DOES and
// LIMITS_CANNOT). tests/test_mobile_app.py, in the server's suite, pins every
// one of them and fails if any drifts from the server's, so each is one plain
// string literal here: no concatenation, no entities, however long the line.
// The polling numbers the portal appends to the second step are left out: the
// app cannot read the configuration before a sign-in.
const STATEMENT =
  'Minas AI Guardian watches your exchange accounts around the clock and tells you within minutes when money starts leaving.';
const LEAD =
  'Read-only monitoring for Bitget, Bybit, OKX and Binance accounts. The key you give Minas can read your account and never trade, withdraw or move anything, so a compromise of Minas itself cannot cost you money.';
const SETUP =
  'Guardian is for Minas members. Minas sets monitoring up with you, then emails you an invitation code to create your sign-in. A member without one? Write to support@minastech.ca.';
const STEPS: readonly (readonly [string, string])[] = [
  ['You make a read-only API key, and Minas connects it.', 'Minas checks with the exchange that the key cannot trade or withdraw, and refuses any key that can.'],
  ['The Guardian keeps reading.', 'Your balances, recent movements, API permissions and allowlists, all day, compared with what it has seen before.'],
  ['When something looks wrong, you are told.', 'On the channels you have set up, with what to do next. A second contact is told too, and Minas escalates if nobody acknowledges.'],
];
const HEADLINE: readonly [string, string] = [
  'Minas AI Guardian cannot stop an attack.',
  'It cannot freeze your account, cancel a transaction, or move funds. When you get an alert, you and the exchange are the ones who can act — and seconds decide the outcome.',
];
const DOES: readonly (readonly [string, string])[] = [
  ['It watches for money leaving.', 'Withdrawals, transfers, and balance drops that no trade explains.'],
  ["It watches your account's security settings.", 'API permissions and IP allowlists changing are how a takeover starts.'],
  ['It tells you, on the channels you have configured.', 'And tells a second contact, and escalates if nobody acknowledges.'],
  ['It keeps the record.', 'Every alert and every check is stored, so you can reconstruct what happened and when afterwards.'],
];
const CANNOT: readonly (readonly [string, string])[] = [
  ['It cannot see what the exchange does not report.', "Every check reads the exchange's own API. If the exchange is slow to show a withdrawal, omits a reference, or is unreachable, the Guardian is that much later or that much blinder. It says so rather than guessing."],
  ['It cannot recover money that has gone.', 'There is no reversal. The Guardian shortens the time between a theft starting and you knowing about it. That is the whole product.'],
];

export default function Landing() {
  const { ended } = useSession();
  const router = useRouter();

  return (
    // No header on this screen, so it keeps clear of the status bar itself.
    <Screen edges={['top', 'left', 'right']}>
      {ended ? (
        <Notice tone="warn" title="This phone was signed out.">
          That happens when the app goes 30 days unopened, when this phone is signed out from the portal&apos;s
          Security page or another phone, after a password change, or when Minas sees this phone&apos;s sign-in
          used from two places at once. If you don&apos;t recognise any of these, change your password on the
          member portal.
        </Notice>
      ) : null}

      <View style={styles.brand}>
        <Image
          source={require('../../assets/images/splash-icon.png')}
          style={styles.mark}
          accessibilityIgnoresInvertColors
        />
        <Text style={styles.name} maxFontSizeMultiplier={1.5}>
          Minas AI <Text style={{ color: colors.gold }}>Guardian</Text>
        </Text>
      </View>

      <View style={styles.group}>
        <Eyebrow>Read-only monitoring for crypto exchange accounts</Eyebrow>
        <Title>{STATEMENT}</Title>
        <Body muted>{LEAD}</Body>
      </View>

      <View style={styles.group}>
        <Button label="Sign in" onPress={() => router.push('/sign-in')} />
        {/* The portal's /welcome, opened in the browser until the app has
            the steps of its own: one gold button per screen, so this one is
            secondary, as on the portal's landing page. */}
        <Button
          label="Accept an invitation"
          variant="secondary"
          onPress={() => Linking.openURL(`${API_URL}/welcome`)}
        />
        <Body muted>{SETUP}</Body>
      </View>

      <Section title="How it works">
        {STEPS.map(([title, text]) => (
          <Card key={title}>
            <Title>{title}</Title>
            <Body muted>{text}</Body>
          </Card>
        ))}
      </Section>

      {/* Standing text, the portal's callout (role="note"): read in place,
          not announced over the statement on every cold start. */}
      <Notice tone="danger" title={HEADLINE[0]} live={false}>
        {HEADLINE[1]}
      </Notice>

      <Section title="What the Guardian does">
        {DOES.map(([title, text]) => (
          <Card key={title}>
            <Title>{title}</Title>
            <Body muted>{text}</Body>
          </Card>
        ))}
      </Section>

      <Section title="What it cannot do">
        {CANNOT.map(([title, text]) => (
          <Card key={title}>
            <Title>{title}</Title>
            <Body muted>{text}</Body>
          </Card>
        ))}
      </Section>

      <Body muted style={styles.phish}>
        {'Minas will never email you a link asking for your password or API key. Sign in only here, or on the portal address you were given.'}
      </Body>

      {/* The portal's footer link (decision 27): the policy is one web page,
          opened in the browser, so there is one copy of its words. */}
      <Pressable accessibilityRole="link" onPress={() => Linking.openURL(`${API_URL}/privacy`)} hitSlop={8}>
        <Text style={styles.link}>Privacy policy</Text>
      </Pressable>
    </Screen>
  );
}

const styles = StyleSheet.create({
  // The mark and the name, as the sign-in screen draws them.
  brand: { alignItems: 'center', gap: space(2) },
  mark: { width: 96, height: 96 },
  name: { color: colors.text, fontSize: type.xl, fontWeight: '700' },
  group: { gap: space(3) },
  phish: { fontSize: type.sm, textAlign: 'center' },
  link: { color: colors.gold, fontSize: type.sm, textAlign: 'center' },
});
