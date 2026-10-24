# Changelog: the Minas AI Guardian app

The app's own record, separate from the server's: it ships through the app
stores, on its own schedule, under `mobile-vX.Y.Z` tags.

## [Unreleased]

### Changed

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
