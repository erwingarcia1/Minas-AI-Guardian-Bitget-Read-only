# Google Play: the store listing and the Data safety answers

Drafted 2026-10-26 for the owner, to copy into Play Console for
`com.minastrade.guardian` (decision 21f). Nothing here is submitted by
anyone but the owner. `tests/test_store_listing.py` holds it to the code:

- The listing's sentences are the portal's, word for word, like the app's
  welcome screen (decision 25). A change to the landing or limits copy
  fails the test until this file follows.
- Each text is inside Google's limit: name 30 characters, short
  description 80, full description 4000.
- The Data safety answers were made against the app's runtime
  dependencies as listed in the last section. Adding a dependency fails
  the test until someone re-checks the answers. An analytics,
  crash-reporting or advertising SDK would change them.

Copy the text inside each block exactly. The privacy policy was approved
for publication on 2026-10-27 (decision 27) and has been live since
v1.19.0 was deployed the same day, so its address can go in the console
now.

## Main store listing

### App name

```text
Minas AI Guardian
```

### Short description

```text
Read-only alerts when money starts leaving your crypto exchange account.
```

### Full description

```text
Minas AI Guardian watches your exchange accounts around the clock and tells you within minutes when money starts leaving.

Read-only monitoring for Bitget, Bybit, OKX and Binance accounts. The key you give Minas can read your account and never trade, withdraw or move anything, so a compromise of Minas itself cannot cost you money.

This app is for Minas AI Guardian members. Minas sets monitoring up with you. There is nothing to sign up for here.

HOW IT WORKS

You connect a read-only API key. Minas checks with the exchange that the key cannot trade or withdraw, and refuses any key that can.

The Guardian keeps reading. Your balances, recent movements, API permissions and allowlists, all day, compared with what it has seen before.

When something looks wrong, you are told. On the channels you have set up, with what to do next. A second contact is told too, and Minas escalates if nobody acknowledges.

IN THE APP

- Your accounts, the incident that needs your attention, and "Am I being watched?" for each one.
- Your alert history, newest first.
- Each incident: the evidence, the timeline, the balances, and what to do next. Acknowledge it from your phone so Minas knows you have seen it.
- Who has touched your accounts: every change to how they are watched or reached.
- Security: whether two-factor is on, the phones signed in to your login, and a way to sign each one out.
- What the Guardian can and cannot do, and how long your data is kept.

WHAT IT CANNOT DO

Minas AI Guardian cannot stop an attack. It cannot freeze your account, cancel a transaction, or move funds. When you get an alert, you and the exchange are the ones who can act — and seconds decide the outcome.

It cannot see what the exchange does not report. Every check reads the exchange's own API. If the exchange is slow to show a withdrawal, omits a reference, or is unreachable, the Guardian is that much later or that much blinder. It says so rather than guessing.

It cannot recover money that has gone. There is no reversal. The Guardian shortens the time between a theft starting and you knowing about it. That is the whole product.

SIGNING IN

You sign in with your email address, your password and a code from your authenticator app. The app keeps one sign-in token in your phone's secure storage. It contains no analytics, advertising or tracking code, and it talks only to the Minas server.

Minas will never email you a link asking for your password or API key.
```

### App category and contact details

| Field | Answer |
|---|---|
| App or game | App |
| Category | Finance |
| Tags | Choose up to five that Play offers near "crypto", "security" and "alerts"; Play's list changes, so pick in the console |
| Email address | support@minastech.ca |
| Website | https://guardian.minastrade.com |
| Phone number | Leave empty |
| Privacy policy | https://guardian.minastrade.com/privacy (live since v1.19.0, 2026-10-27) |

### Graphics

| Asset | File | Spec |
|---|---|---|
| App icon | [`play-icon.png`](play-icon.png) | 512 × 512, 32-bit PNG. The launcher icon's composition; Play rounds the corners itself |
| Feature graphic | [`play-feature-graphic.png`](play-feature-graphic.png) | 1024 × 500, 24-bit PNG, no transparency. The mark, "Minas AI Guardian" and the landing page's eyebrow line |
| Phone screenshots | Not made | Two to eight. Take them on a phone signed in to a test account, not a customer's: alerts and balances are personal data |

Both images come from `design/brand/minas-logo.png` by
`python3 scripts/brand-assets.py store`. Never edit them by hand.
`tests/test_brand_assets.py` holds their sizes and keeps the feature
graphic's line equal to the landing page's.

## Data safety

Google's terms, from its Data safety help page:

- **Collected:** data the app sends off the phone.
- **Shared:** data handed to a third party. Data sent to a service provider that processes it for Minas, such as Amazon Web Services, which runs the server, is not "shared".

The app sends data to one place, the Minas server.

### Data collection and security

| Question | Answer | Why |
|---|---|---|
| Does your app collect or share any of the required user data types? | Yes | The sign-in sends the email address and the phone's name |
| Is all of the user data collected by your app encrypted in transit? | Yes | The app talks only to the server over HTTPS |
| Do you provide a way for users to request that their data is deleted? | Yes | The portal's Security → Delete my data, or an email to support@minastech.ca. The app itself cannot delete, so the web link below is the way |
| Account creation (if the form asks) | The app does not let users create an account | Minas creates every account. The app only signs in, and directs nobody to a sign-up: "There is nothing to sign up for here" |
| Delete-account link (if the form asks) | https://guardian.minastrade.com/privacy#privacy-rights | The privacy policy's "Your choices" section names Minas AI and says how to delete. Google wants the deletion part reachable directly, hence the anchor |

### Data types

Declare these three; everything else is "not collected".

| Data type | Collected | Shared | Processed ephemerally | Required or optional | Purposes | What it is |
|---|---|---|---|---|---|---|
| Personal info → Email address | Yes | No | No | Required | App functionality; Account management; Fraud prevention, security, and compliance | The address you sign in with. The server stores your login under it |
| Device or other IDs | Yes | No | No | Required | Account management; Fraud prevention, security, and compliance | The phone's name, and the sign-in token that identifies this installation. They are listed under "Phones signed in to the app" so you can sign a phone out. Declared here to be safe: the name is not an ID in Google's sense, but on Android it is the name the owner gave the phone, which may contain theirs |
| App activity → Other actions | Yes | No | No | Optional | App functionality | Acknowledging an alert, and signing a phone out. The server records who did it and when |

What the app sends but you do not declare:

- **The password and the two-factor code.** They are sent to sign in and are not a data type on the form. The server keeps only a scrypt hash of the password and never stores the code. The app stores neither.

What the app does not collect, though you might expect it to:

- **Financial info.** Balances, withdrawals and trades reach the app from the server, which reads them from the exchange with the read-only key Minas set up with you. The app displays them and sends nothing financial off the phone. The key never touches the app. If Google's reviewer disagrees, the honest fallback is Financial info → Other financial info, collected, required, for App functionality.
- **Phone number, name, location, contacts, photos, files, messages.** The app never asks for them. Contact details are set with Minas, on the portal or by email.
- **Crash logs, diagnostics, analytics.** The app contains no SDK that sends them.

### Security practices (the optional badges)

| Practice | Answer |
|---|---|
| Data is encrypted in transit | Yes |
| You can request that data be deleted | Yes |
| Committed to follow the Families Policy | No: the app is not for children |
| Independent security review | No |

## The rest of App content, for reference

| Item | Answer | Note |
|---|---|---|
| Privacy policy | https://guardian.minastrade.com/privacy | Approved 2026-10-27; live since v1.19.0, deployed the same day |
| Ads | No, the app does not contain ads | |
| App access | Open (decision 28) | Google's reviewer needs a login that gets past two-factor without a live code. The owner decides this after internal testing |
| Content rating | Answer the questionnaire honestly | No violence, sexual content, profanity, gambling or user-to-user contact. Expect the lowest rating. The app shows financial account data, not trading or gambling |
| Target audience | 18 and over | The policy's minimum age (decision 27), the same as the four exchanges require of their own account holders |
| News app | No | |
| Health app | No | |
| Government app | No | |
| Financial features | Choose what is true in the console's list | The app does not buy, sell, hold, exchange or transfer crypto. It offers no payments, loans or trading. It reads an account and tells you. Google's crypto rules cover exchanges and wallets, and this is neither |
| Data safety | As above | |

## Runtime dependencies the Data safety answers were made against

`mobile/package.json` `dependencies`, by name, on 2026-10-26:

```text
expo
expo-constants
expo-device
expo-font
expo-glass-effect
expo-linking
expo-router
expo-secure-store
expo-splash-screen
expo-status-bar
expo-system-ui
openapi-fetch
react
react-native
react-native-safe-area-context
react-native-screens
```
