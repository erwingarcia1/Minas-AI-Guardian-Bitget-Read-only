# Standing instructions — Minas AI Guardian

Read [CONTRIBUTING.md](CONTRIBUTING.md) before any code change. The hard
rules, release process, and verification gates live there and are
deliberately not duplicated here.

## Owner's operating constraints

- **Never deploy.** Deploys are manual and the owner's.
- **Never cut a release on your own initiative.** The owner decides when a
  release is cut and when it ships. Keep writing `[Unreleased]` notes in
  CHANGELOG.md as you close each issue — that is required. When the owner
  instructs a release by name, run the WHOLE cut: `scripts/release.sh`,
  create and push the annotated tag, confirm CI — tagging is part of the
  cut, not a separate permission. The only two acts reserved to the owner
  are the instruction to cut and the deployment itself. (Standing as of
  2026-08-21; supersedes any earlier run-brief language about cutting when
  a batch is done.)
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
