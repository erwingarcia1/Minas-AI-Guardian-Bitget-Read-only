# Minas AI Guardian: the iOS and Android app

The customer app (decision 21 in `docs/DECISIONS-NEEDED.md`). It uses Expo
SDK 58, React Native, Expo Router and TypeScript. It talks only to the JSON
API under `/api/v1/`, which the member portal serves. It is for members:
nothing here can add a customer, read another customer's data or change a
threshold.

## Run it

```bash
cd mobile
npm ci
npm start              # then scan the QR code with Expo Go, or press i / a for a simulator
```

The app talks to `https://guardian.minastrade.com` (decision 22). To use a
local server instead:

```bash
EXPO_PUBLIC_API_URL=http://<your-machine's-LAN-IP>:8391 npm start
```

Use the LAN IP, because a phone cannot reach `localhost` on your machine.
The portal must listen on that address (`SENTINEL_PORTAL_HOST`). Sign in
with a portal login that has two-factor turned on; the app refuses any
other (21c).

## What it does

| Screen | API |
|---|---|
| Sign in: email, password and two-factor code | `POST /api/v1/session` |
| Accounts: "Am I being watched?", the portal's exact words | `GET /api/v1/accounts` |
| Alerts: newest first, with older ones on request | `GET /api/v1/alerts` |
| Incident: evidence, timeline, balances, what to do next, acknowledge | `GET /api/v1/alerts/{id}`, `POST .../acknowledge` |
| Security: two-factor state, signed-in phones, sign out | `GET /api/v1/security`, `DELETE /api/v1/devices/{id}`, `DELETE /api/v1/session` |
| Who has touched your accounts | `GET /api/v1/activity` |
| What the Guardian can and cannot do | `GET /api/v1/limits` |

## How it is held together

- **The API types are generated** from `../api/openapi.yaml`, the contract
  `tests/test_api_contract.py` holds the server to. Run `npm run gen:api`
  and commit the result. CI regenerates the file and fails on any
  difference. The generator runs through `npx` at pinned versions with its
  own TypeScript 5, because `openapi-typescript` does not support
  TypeScript 6 yet.
- **Sign-in:** the refresh token lives in the Keychain or Keystore
  (`expo-secure-store`); the 15-minute access token lives in memory only.
  The server revokes a device whose replaced refresh token comes back, so
  the app makes only one refresh at a time and every waiting request shares
  it. See `src/auth/tokens.ts` and its tests.
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
npm run typecheck
npm test
npm run audit:check
npx expo export --platform ios --platform android   # both bundles compile
```

## Not done yet

- **Push notifications:** waiting on decision 21e.
- **App identity:** the iOS bundle identifier and Android package name
  depend on which legal entity publishes the app, which is the owner's
  call (21f). They are needed before the first store build.
- **Store accounts and builds:** Apple Developer Program, Google Play, and
  EAS Build. The publisher's identity verification takes time, so start it
  early.
- **Account changes:** the settings only the account's main contact may
  change come with API v2.

Releases of the app take their own tags (`mobile-vX.Y.Z`) and their own
`CHANGELOG.md` here. The server's deploy gate accepts only `vX.Y.Z` tags.
