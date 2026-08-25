#!/usr/bin/env python3
"""The survivors the mutation gate has accepted, and a hard stop on any other.

    scripts/mutation_survivors.py <survivors-file> [--check]

Proven 2026-08-25, twice (a warm mutants/ and a #97 restore): delete one
assertion from a selected test file, touch nothing under sentinel/, run the
gate. The mutant that assertion killed is re-judged - every mutant the gate
names is, restore or not - and comes back a survivor: 185/188 instead of
186/188. And the gate PASSES, because 98% clears a 96% floor. The ratchet's
two points of headroom exist so ordinary change does not trip the gate; the
price is that one decision can quietly stop being pinned, and a score alone
cannot tell that from the two equivalent mutants everyone already knows about.

So the gate keeps a ledger. `scripts/mutation-known-survivors.txt` lists each
survivor that has been looked at and accepted, with the reason. A survivor
not in it fails `--check` outright, whatever the score says: either a test
stopped pinning a decision, or a new decision arrived with nothing pinning
it, and both are news. Accepting one is a deliberate commit to the ledger
with the reason written down, never a side effect of a passing run.

Keyed by file plus the mutation diff, not by mutant name: numbering is not
stable across regenerations (mutation-check.sh quirk 5), and a ledger that
had to be rewritten every rebuild would be rewritten by copy-paste.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "scripts" / "mutation-known-survivors.txt"


def _mutmut() -> str:
    venv = REPO / ".venv" / "bin" / "mutmut"
    return str(venv) if venv.exists() else "mutmut"


def show(name: str) -> str:
    """mutmut's diff for one mutant. Raises on failure - a survivor whose
    diff cannot be read cannot be matched to the ledger, and unverifiable
    is unaccepted."""
    completed = subprocess.run([_mutmut(), "show", name], capture_output=True, text=True,
                               cwd=REPO, check=False)
    if completed.returncode != 0 or "+++ " not in completed.stdout:
        raise RuntimeError(f"mutmut show {name} failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout


def fingerprint(diff: str) -> tuple[str, str, list[str]]:
    """(fingerprint, file, changed lines) from a `mutmut show` diff. Only the
    file and the -/+ lines count: hunk headers carry line numbers, which
    move whenever anything above the mutation is edited."""
    file = ""
    changed = []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            file = line[4:].strip()
        elif line.startswith(("--- ", "+++ ", "@@", "#")):
            continue
        elif line.startswith(("-", "+")):
            changed.append(line[0] + line[1:].strip())
    if not file or not changed:
        raise ValueError("no mutation found in diff")
    digest = hashlib.sha256((file + "\n" + "\n".join(changed)).encode()).hexdigest()[:16]
    return digest, file, changed


REASON_KINDS = ("equivalent", "unreachable", "deliberate")


def load_ledger(path: Path | None = None) -> dict[str, str]:
    """fingerprint -> reason. Blank lines and # comments are ignored.

    Every row carries its reason on the same line, and the reason starts by
    naming its kind - equivalent, unreachable or deliberate - followed by
    the argument. A row without one fails the gate: adding a row is easier
    than writing a test, and each addition looks legitimate on the day, so
    the row itself has to say why, where the next reader will see it."""
    path = path or LEDGER
    entries = {}
    if not path.exists():
        return entries
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        reason = parts[2].strip() if len(parts) == 3 else ""
        kind = reason.split(":", 1)[0].strip().lower() if ":" in reason else ""
        if kind not in REASON_KINDS or not reason.split(":", 1)[1].strip():
            raise ValueError(
                f"ledger row has no reason: {raw!r}\n"
                f"  each row is <fingerprint> <file> <kind>: <why>, kind one of "
                f"{', '.join(REASON_KINDS)} - a survivor accepted without a reason is not accepted")
        entries[parts[0]] = reason
    return entries


def review(names: list[str], ledger: dict[str, str], shower=None) -> list[str]:
    """Prints one line per survivor; returns the unaccepted ones."""
    shower = shower or show
    unaccepted = []
    for name in names:
        digest, file, changed = fingerprint(shower(name))
        if digest in ledger:
            print(f"  accepted   {digest}  {name}\n             {ledger[digest]}")
        else:
            unaccepted.append(name)
            print(f"  NEW        {digest}  {name}")
            for line in changed:
                print(f"             {line}")
    return unaccepted


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--check"]
    check = "--check" in argv
    if len(args) != 1:
        print("usage: scripts/mutation_survivors.py <survivors-file> [--check]", file=sys.stderr)
        return 2
    names = [line.strip() for line in Path(args[0]).read_text().splitlines() if line.strip()]
    # The ledger is validated and its size stated on EVERY run, survivors or
    # not: a number that only ever goes up is noticed in a weekly report; a
    # file someone has to open is not.
    try:
        ledger = load_ledger()
    except ValueError as exc:
        print(f"survivor ledger is malformed: {exc}", file=sys.stderr)
        return 1
    print(f"  Survivor ledger: {len(ledger)} accepted")
    if not names:
        print("  no survivors")
        return 0
    try:
        unaccepted = review(names, ledger)
    except (RuntimeError, ValueError) as exc:
        print(f"survivor could not be matched to the ledger: {exc}", file=sys.stderr)
        return 1
    if unaccepted:
        print(f"\n{len(unaccepted)} survivor(s) not in {LEDGER.relative_to(REPO)}: a decision this suite "
              f"used to pin, it no longer does - or a new one nobody pinned. Kill it with a test, "
              f"or if it is a proven equivalent, add its fingerprint and the reason to the ledger.",
              file=sys.stderr)
        return 1 if check else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
