# Changelog: the Minas AI Guardian app

The app's own record, separate from the server's: it ships through the app
stores, on its own schedule, under `mobile-vX.Y.Z` tags.

## [Unreleased]

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
