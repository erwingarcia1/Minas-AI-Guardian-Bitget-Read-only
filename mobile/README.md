# Minas AI Guardian: the iOS and Android app

The customer app (decision 21 in `docs/DECISIONS-NEEDED.md`). It uses Expo
SDK 58, React Native, Expo Router and TypeScript. It talks only to the JSON
API under `/api/v1/`, which the member portal serves. It is for members:
nothing here can add a customer, read another customer's data or change a
threshold.

## Try it on your phone

The app talks to `https://guardian.minastrade.com` unless told otherwise,
and the app API (`/api/v1/`) ships in the server from v1.13.0. Until that
release is deployed and the address answers, a plain `npm start` has
nothing to sign in to; once it is, signing in there needs a real member
login with two-factor on. For either case, run the demo server on your
computer instead and point the app at it. The demo server has a demo
login, fake accounts and alerts, and makes no outside connections.

1. **Phone and computer on the same Wi-Fi.**
2. **Expo Go 58 or later** on the phone, from the App Store or Google Play.
   The app is SDK 58, and Expo Go 57 refuses to open it. On Android, if
   the store update has not arrived, plug the phone in with USB debugging
   on and press `a` in step 5. The Expo CLI then installs the right Expo
   Go itself.
3. **Let the phone reach this computer.** A firewall that drops incoming
   connections blocks the phone. On Ubuntu with `ufw`, allow your home
   network (here `192.168.1.0/24`) to reach the demo server (8391) and the
   bundler (8081):

   ```bash
   sudo ufw allow from 192.168.1.0/24 to any port 8391,8081 proto tcp
   # when you are done testing:
   sudo ufw delete allow from 192.168.1.0/24 to any port 8391,8081 proto tcp
   ```

4. **Start the demo server**, from the repository root:

   ```bash
   .venv/bin/python scripts/dev-server.py
   ```

   It prints the command for the next step with this computer's address in
   it. It also prints the demo login and a fresh two-factor code every 30
   seconds. Add `--new-alert-every 60` to see new alerts arrive.
5. **Start the app**, in a second terminal, with the command step 4
   printed:

   ```bash
   cd mobile && npm ci && EXPO_PUBLIC_API_URL=http://<this computer's address>:8391 npx expo start --clear
   ```

6. **iPhone only:** Expo Go opens a project from your computer only when
   Expo Go and the CLI are signed in to the same Expo account. Run
   `npx expo login` (check with `npx expo whoami`), sign in to the same
   account in Expo Go, then restart step 5.
7. **Scan the QR code**: with the Camera app on an iPhone, or from inside
   Expo Go on Android. Allow the phone to find devices on your local
   network when it asks.
8. **Sign in** with the demo email and password and the code the demo
   server printed. The sign-in screen shows which server the build talks
   to.

Saving a file reloads the app on the phone. Avoid reloading while the
app is starting up. A reload in the middle of a sign-in refresh can sign
the phone out, because the server takes the second try for a stolen token
(decision 21h). If that happens, sign in again.

The portal is on the same demo server, at `http://localhost:8391` on this
computer only. Its sign-in cookie is `Secure`, which browsers allow over
plain HTTP for `localhost` only.

## What it does

| Screen | API |
|---|---|
| Sign in: email, password and two-factor code | `POST /api/v1/session` |
| Accounts: the incident needing attention, any pending pause, and "Am I being watched?" in the portal's exact words | `GET /api/v1/accounts` |
| Alerts: newest first, with older ones on request | `GET /api/v1/alerts` |
| Incident: evidence, timeline, balances, what to do next, acknowledge | `GET /api/v1/alerts/{id}`, `POST .../acknowledge` |
| Security: two-factor state, signed-in phones, sign out | `GET /api/v1/security`, `DELETE /api/v1/devices/{id}`, `DELETE /api/v1/session` |
| Who has touched your accounts | `GET /api/v1/activity` |
| What the Guardian can and cannot do | `GET /api/v1/limits` |

## How it is held together

- **The API types are generated** from `../api/openapi.yaml`, the contract
  `tests/test_api_contract.py` holds the server to. Run `npm run gen:api`
  and commit the result. CI regenerates the file and fails on any
  difference. The generator is installed from its own lockfile
  (`tools/openapi/`), down to its last dependency, with its own TypeScript
  5, because `openapi-typescript` does not support TypeScript 6 yet.
- **Sign-in:** the refresh token lives in the Keychain or Keystore
  (`expo-secure-store`), and is never restored onto another phone from a
  backup. On an iPhone it is readable only while the phone is unlocked;
  Android's Keystore key, as expo-secure-store builds it, can be read on
  a locked phone once it has been unlocked since boot. It is stored per
  server, so a development build never presents the real server's token
  to a laptop. The 15-minute access
  token lives in memory only. The server revokes a device whose replaced
  refresh token comes back, so the app makes only one refresh at a time,
  and every waiting request shares it. Everything in flight belongs to
  the sign-in it started under: after a sign-out, nothing from the old
  sign-in goes out on a new one's token. A request is never sent without
  a token, and a token pair is checked before it is stored. When none can be had, the screen says why: no connection,
  Minas's trouble, or the sign-in has ended. See `src/auth/tokens.ts` and
  its tests.
- **Every call has a deadline** (`src/api/network.ts`): 15 seconds for a
  read, 30 for a refresh, and 45 for signing in and other writes, which
  the server may answer only after delivering something. The deadline is
  kept in JavaScript, not left to the platform's cancellation, and an
  answer shorter than its `Content-Length` counts as a dropped connection.
  Signing out of the phone waits at most 5 seconds for the server.
- **Screens keep themselves current** (`src/api/use-request.ts`): each one
  reloads, without a spinner, when it comes back into view or the app
  comes back to the front. The Accounts tab also asks again every 30
  seconds. It never shows a verdict older than the monitoring window it
  came with; it shows "Checking again…" instead.
- **Tests** render the whole app with its real routes against a fake
  server at the network boundary (`src/test-utils/`), one file per area
  in `src/__tests__/`. The token rules have their own tests in
  `src/auth/__tests__/`.
- **Colours** are the portal's own `:root` tokens (`src/theme/tokens.json`).
  **Icons** are copies that `scripts/brand-assets.py derive` writes from
  `design/brand/`. `tests/test_mobile_app.py`, in the server's suite, fails
  if either drifts.
- **The dependency audit** is `npm run audit:check`. Every high or critical
  advisory in the production tree must be listed in `audit-allowlist.json`
  with its reason. An unlisted one fails CI, and so does a listed one that
  has gone away.
- **npm install scripts** are denied for `@parcel/watcher` and
  `unrs-resolver` (`allowScripts` in `package.json`). Both are build tools,
  and the bundle builds without their scripts.

## Checks

```bash
npm run typecheck      # generates the typed routes first, so a broken link fails
npm test
npm run audit:check
npx expo export --platform ios --platform android   # both bundles compile
```

## Building for the stores

`eas.json` has two profiles, built with EAS Build (`npx eas-cli@latest`,
signed in to the Expo account):

```bash
npx eas-cli@latest build --profile preview --platform android   # an APK to install directly, for testing
npx eas-cli@latest build --profile production --platform all    # store builds, numbered by EAS
npx eas-cli@latest submit --profile production --platform ios   # to App Store Connect (TestFlight first)
```

The production build talks to `https://guardian.minastrade.com`, so that
address must be serving a release with the app API first. An iOS build
of any kind needs the Apple Developer Program account. The Android
preview APK needs neither store account.

## Not done yet

- **Push notifications:** waiting on decision 21e.
- **Store accounts.** The app is published as `com.minastrade.guardian`
  (21f), but neither the Apple Developer Program nor the Google Play
  account exists yet. Enrol the company in both; identity verification
  takes days. Google also asks a new personal account for a 14-day
  closed test before production, and an organisation account avoids
  that.
- **Account changes:** the settings only the account's main contact may
  change come with API v2.

Releases of the app take their own tags (`mobile-vX.Y.Z`) and their own
`CHANGELOG.md` here. The server's deploy gate accepts only `vX.Y.Z` tags.
