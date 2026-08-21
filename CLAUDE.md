# Standing instructions — Minas AI Guardian

Read [CONTRIBUTING.md](CONTRIBUTING.md) before any code change. The hard
rules, release process, and verification gates live there and are
deliberately not duplicated here.

## Owner's operating constraints

- **Never deploy.** Deploys are manual and the owner's.
- **Never cut a release on your own initiative.** The owner decides when a
  release is cut and when it ships. Keep writing `[Unreleased]` notes in
  CHANGELOG.md as you close each issue — that is required — but do not run
  `scripts/release.sh`, do not create or push tags, and do not deploy
  unless the owner asks for a release by name. (Standing as of 2026-08-21;
  supersedes any earlier run-brief language about cutting when a batch is
  done.)
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
