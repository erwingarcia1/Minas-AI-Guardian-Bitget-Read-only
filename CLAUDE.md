# Standing instructions — Minas AI Guardian

Read [CONTRIBUTING.md](CONTRIBUTING.md) before any code change. The hard
rules, release process, and verification gates live there and are
deliberately not duplicated here.

## Owner's operating constraints

- **Never deploy.** Deploys are manual and the owner's.
- **Never touch `.env` or credentials** beyond what an issue explicitly
  requires.
- **Never run a second session against this working tree.** Confirm you are
  alone before starting an issue (standing rule in docs/AUTONOMOUS-LOG.md).
- **Never use `--no-verify`.**
- **Never leave `main` with a failing suite.**
- **Billing and payments:** write the implementation plan into the issue;
  write no code.
- **Check CI after every push** (`gh run list --limit 1`). Red is a stop
  condition — do not start the next piece of work until it is green.

## Context

Session handoffs: docs/AUTONOMOUS-LOG.md. Blocked product decisions:
docs/DECISIONS-NEEDED.md.
