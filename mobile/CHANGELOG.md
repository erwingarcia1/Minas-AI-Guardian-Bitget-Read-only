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
  trade at a time. The app wears the Minas logo and the portal's colours.
  It has no push notifications yet (21e).
