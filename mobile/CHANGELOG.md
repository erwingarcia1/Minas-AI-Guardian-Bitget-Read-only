# Changelog: the Minas AI Guardian app

The app's own record, separate from the server's: it ships through the app
stores, on its own schedule, under `mobile-vX.Y.Z` tags.

## [Unreleased]

### Added

- **A "Privacy policy" link on the welcome, sign-in and Settings
  screens (decision 27).** It opens the portal's `/privacy` page in the
  browser, so there is one copy of the words; Google Play wants the policy
  reachable in the app. The portal links to it from the same places (its
  footer and its sign-in page). `tests/test_mobile_app.py` pins the three
  links.

### Changed

- **Version 1.0.0, built for Google Play.** The first store build goes to
  Play's internal testing track for the team, then to production after
  Google's review; the `mobile-v1.0.0` tag goes on the build that reaches
  production. `app.json` names the EAS project (`jimroxodezis-team`'s
  `minas-ai-guardian`), which holds the Android upload key; EAS numbers
  the builds (versionCode 1 was the first preview APK).

- **Android no longer asks for storage or "display over other apps".**
  Expo's template requests READ/WRITE_EXTERNAL_STORAGE and
  SYSTEM_ALERT_WINDOW; the app reads no files and draws no overlays, and
  the first preview build asked for all three. `android.blockedPermissions`
  removes them, so the app requests INTERNET and VIBRATE, plus the
  biometric pair expo-secure-store declares.
  `tests/test_mobile_app.py` pins the list.

- **The activity screen names its one exception (decision 6b).** "This
  record is append-only" now goes on: when a person named in it asks
  Minas to delete their data, their email address, phone number and
  Telegram identity are replaced with [erased], and an entry says so. The
  sentence is the portal's `ACTIVITY_PROMISE`, word for word;
  `tests/test_mobile_app.py` pins the copy. The new entry ("A person's
  details were erased at their request") arrives through
  `/api/v1/activity` like every other. Not in the first internal-test
  build (versionCode 2, built from 4ccfa99 before this landed); the next
  build carries it.

- **An ended sign-in lands on the welcome screen, which says why.** When
  the server ends a phone's sign-in (signed out from the portal or another
  phone, 30 days unopened, a password change, a sign-in used from two
  places at once), the app used to drop onto the sign-in form with the
  "This phone was signed out." notice above it. It now lands on the
  welcome screen, with that notice at the top, and every member screen
  leaves the stack: there is no way back to member data. Signing out from
  Security lands on the same screen without the notice, as before. The
  sign-in form is reached from the welcome screen's "Sign in" button and
  has a header now, so it has a way back; opened directly (a link to
  `/sign-in`) it has none, as before. The form is declared singular
  (`dangerouslySingular`), so a fast double tap on that button opens one
  form, not two stacked, and one Back returns to the welcome screen. The
  guard is still `Stack.Protected`,
  now with an explicit `redirectTo`: expo-router 58's own fallback picks
  the shortest unguarded route name, and "landing" and "sign-in" tie, so
  the target is named rather than left to an alphabetical tie-break. The
  comment in `src/app/_layout.tsx` records the verification, file by file.

- **Headings and standing text read right to a screen reader.** `Title`
  is a heading now (`accessibilityRole="header"`), as the portal's `<h1>`
  is: VoiceOver's and TalkBack's heading list on the welcome screen starts
  with what the Guardian is, not with "How it works", and on every other
  screen each card's title heads its card. The limits headline ("Minas AI
  Guardian cannot stop an attack.") is standing text, the portal's
  `role="note"` callout, so it is no longer announced aloud on every cold
  start of the welcome screen and every opening of the limits screen:
  `Notice` takes `live={false}` for text that is there from the start, and
  keeps the alert role and the announcement for what happens while a screen
  is open (an ended sign-in, a failed request, a refused sign-out).

- **The `braces` advisory left the audit allowlist.** GitHub withdrew
  GHSA-vfj7-8cjw-p6xm on 2026-10-23, so `npm audit` no longer reports it
  and `audit-check.mjs` refused the stale entry, as it is built to: an
  allowlist row that nothing matches is a claim nobody is checking. The
  lockfile is unchanged (braces 3.0.3). node-forge stays listed, with its
  reason.

- **The `zod` advisory joined the audit allowlist.** GHSA-mrhx-c7xc-m3w5
  (high: memory exhaustion while validating oversized constrained
  collections) started appearing in `npm audit` on 2026-10-23 and failed
  the check as unlisted. zod 3.25.76 is in the tree for `@expo/cli` alone,
  which validates its own config and manifests at build and dev time; it is
  not in the app bundle. The patched release is 4.6.6, a different major
  that the CLI's `^3.25.76` cannot take, and no later 3.x release is
  patched, so the row records the reason and the review date instead of a
  blind major override on a build tool. The lockfile is unchanged.

- **Nine template dependencies left `package.json`.** `@expo/ui`,
  `expo-image`, `expo-symbols`, `expo-web-browser`, `react-dom`,
  `react-native-web`, `react-native-gesture-handler`,
  `react-native-reanimated` and `react-native-worklets` came with the
  `create-expo-app` template, for web builds, native tabs, glass and
  symbol views, images, an in-app browser and the gesture and animation
  stacks. No screen imports any of them, the layouts use only `Stack` and
  `Tabs`, and the app is built for iOS and Android only. Both bundles
  still export and the tests still pass without them, also with the four
  that stay in the lockfile because packages still in the tree name them
  as required peers (`react-native-drawer-layout`, under `expo-router`,
  needs gesture-handler and reanimated; reanimated needs worklets; the
  `@radix-ui` packages need `react-dom`) taken out of `node_modules`.
  `expo-system-ui` stays although nothing imports it: its
  config plugin is what applies app.json's dark interface style and
  background colour, which `tests/test_mobile_app.py` pins. 24 packages
  left the lockfile.

### Added

- **A welcome screen (decision 25).** A phone with no sign-in opens on
  what the Guardian is, in the portal's words: read-only monitoring, what
  it watches and tells, what it cannot do, how it works in three steps,
  that Minas sets monitoring up with the member (there is nothing to sign
  up for), the anti-phishing line, and one gold "Sign in". It asks nothing
  of the server. The sentences are copies of `sentinel/portal.py`'s, and
  `tests/test_mobile_app.py` in the server's suite fails if they drift;
  the polling numbers the portal appends to its second step are left out,
  because the app cannot read the configuration before a sign-in.

- **The first version of the app (decision 21).** Expo SDK 58 and React
  Native 0.88, for iOS and Android. It covers:
  - sign-in with email, password and two-factor code;
  - "Am I being watched?" for every account, in the portal's words;
  - the alert list;
  - each incident, with its evidence, timeline, balances and what to do
    next;
  - acknowledging an alert;
  - the devices signed in to the login, with sign-out;
  - the activity record and the limits page.

  The refresh token is kept in the Keychain or Keystore and refreshed one
  trade at a time, and one sign-in never lends its token to another. It
  is stored per server and is never restored onto another phone from a
  backup; on an iPhone it is also readable only while the phone is
  unlocked. Every call has a deadline, so a dead network shows a message,
  not a spinner, and an answer cut short is never taken for a whole one.
  Signing out of the phone waits at most 5 seconds for the server, then
  forgets the sign-in either way. Screens reload by themselves when they
  come back into view or the app comes back to the front. The Accounts tab
  asks again every 30 seconds and never shows a verdict older than its
  monitoring window. Like the portal's home page, the Accounts tab leads
  with the newest incident nobody has acknowledged and any pause about to
  stop monitoring, and a refused key says where it is replaced and by
  whom. The app wears the Minas logo and the portal's colours. It has no
  push notifications yet (21e).

  The app is published as `com.minastrade.guardian` (21f), with EAS build
  profiles for a directly installable Android preview and for the
  stores.
