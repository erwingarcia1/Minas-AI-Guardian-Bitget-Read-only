# Standing instructions — Minas AI Guardian

Read [CONTRIBUTING.md](CONTRIBUTING.md) before any code change. The hard
rules, release process, and verification gates live there and are
deliberately not duplicated here.

## Owner's operating constraints

- **Never deploy.** Deploys are manual and the owner's.
- **Never cut a release on your own initiative.** The owner decides when a
  release is cut and when it ships. Work issues, commit, push, confirm CI —
  then stop and say what is unreleased. Running `scripts/release.sh` is
  gated on the owner, the same way deploying is. The `[Unreleased]`
  CHANGELOG entry is still written as part of each issue — notes must never
  be reconstructed later from commit messages. (Supersedes any earlier run
  brief that said to cut a release when a coherent batch is done;
  standing as of 2026-08-21.)
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
