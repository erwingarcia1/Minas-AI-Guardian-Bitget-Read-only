<!-- Minas AI Guardian — pull request -->

## What & why

<!-- What does this change, and why? Link any issue. -->

## Checklist

- [ ] Tests added/updated; `pytest -q` passes locally
- [ ] If this touches onboarding / exchange clients / the read-only gate: the
      fail-closed guarantee holds, with **trigger and non-trigger** tests
- [ ] No secrets in code/config/logs; `.env`, `sentinel_data/`, `*.pem` still ignored
- [ ] `Decimal` for money/quantity, UTC for timestamps (if applicable)
- [ ] New exchange endpoints/fields cited from live CCXT/exchange docs (not memory)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`

## Notes for the reviewer

<!-- Anything that needs extra eyes, or follow-ups deliberately left out. -->
