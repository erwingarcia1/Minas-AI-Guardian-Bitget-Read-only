#!/usr/bin/env python3
"""Hand-written mutation probes that cannot silently fail to apply.

    scripts/mutation-probe.py probes.json

`scripts/mutation-check.sh` covers what mutmut can generate: comparisons,
booleans, control flow. It cannot cover a *design* guard - "the account scope
is inside the UPDATE", "the recorder failing must not stop the reload",
"attribution is required once operators exist". Those are checked by hand:
break the guard, run the tests, put it back.

Which is fine until a hand-written mutation doesn't apply. A `sed` whose
pattern doesn't match, or a replacement string whose indentation is off by four
spaces, changes nothing - and an unchanged file passes its tests. That reads
exactly like a test killing the mutant. It is the same trap the mutation score
exists to close, one level up: an absence of evidence presented as evidence.

So this refuses to run tests until it has proved the source actually changed,
and it fails the whole run if any probe didn't land. A probe that cannot be
applied is a broken probe, not a passing one.

## The probe file

A JSON list. `old` must appear exactly once in the file - a pattern matching
twice is ambiguous about what was tested, and matching zero times is the bug
this tool exists to catch.

    [
      {
        "name": "drop the account scope from the UPDATE",
        "file": "sentinel/portal.py",
        "old": "WHERE id = ? AND acknowledged_at IS NULL AND account_id IN",
        "new": "WHERE id = ? AND acknowledged_at IS NULL",
        "tests": ["tests/test_portal_acknowledge.py"],
        "expect": "killed"
      }
    ]

`expect` is optional and defaults to `killed`. Set it to `survived` for a probe
you have decided is an equivalent mutation - it then fails if a test ever
starts killing it, which means the claim of equivalence has stopped being true.

Exit status is 0 only when every probe landed and every probe matched its
expectation.
"""
from __future__ import annotations

import atexit
import fcntl
import json
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCKFILE = REPO / ".mutation-probe.lock"


def _take_the_lock():
    """Refuse to run while another probe run is patching the same files.

    Two concurrent runs corrupt the working tree, and they do it in the one
    way this tool is otherwise careful to prevent: silently. Each run copies a
    file aside before mutating it, so if run B takes its backup while run A has
    a mutation applied, B's backup *is* A's mutant - and B faithfully restores
    it when it finishes. What is left on disk is a real-looking source edit
    nobody made, in a tree that `git status` says is dirty for a reason you
    will assume was you. Observed here: `snapshot_at <= ?` left as
    `snapshot_at >= ?` in an incident-balance query.

    Refuse rather than queue. Waiting would mean an unattended `--check` in a
    hook or a health check blocking for however long the other run takes; the
    honest answer is "somebody else is doing this, try again".

    The lock is advisory and process-scoped: the kernel drops it when this
    process dies however it dies, so a killed run cannot leave it held. The
    handle is deliberately kept alive for the process lifetime.
    """
    handle = LOCKFILE.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _pytest() -> list[str]:
    venv = REPO / ".venv" / "bin" / "python"
    return [str(venv) if venv.exists() else sys.executable, "-m", "pytest", "-q",
            "-p", "no:cacheprovider"]


def _apply(path: Path, old: str, new: str) -> str:
    """Returns the original text. Raises if the mutation would be a no-op."""
    original = path.read_text()
    occurrences = original.count(old)
    if occurrences == 0:
        raise ValueError(f"pattern not found in {path} - the probe is stale, not passing")
    if occurrences > 1:
        raise ValueError(f"pattern appears {occurrences} times in {path} - ambiguous probe")
    mutated = original.replace(old, new, 1)
    if mutated == original:
        raise ValueError(f"replacement is identical to the original in {path} - nothing mutated")
    path.write_text(mutated)
    # Belt and braces: prove the bytes on disk differ from what we started
    # with, rather than trusting the string comparison above. A probe whose
    # `new` differs only in trailing whitespace that an editor or a formatter
    # then strips would otherwise report as applied.
    if path.read_text() == original:
        path.write_text(original)
        raise ValueError(f"{path} is unchanged on disk after writing - mutation did not land")
    return original


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        print("usage: scripts/mutation-probe.py <probes.json>", file=sys.stderr)
        return 2

    lock = _take_the_lock()
    if lock is None:
        print(f"another probe run holds {LOCKFILE.name} - it is patching these same "
              f"files, and two runs at once leave mutations behind. Try again after "
              f"it finishes.", file=sys.stderr)
        return 2

    probes = json.loads(Path(argv[1]).read_text())
    results, failures = [], 0

    # A probe run that is killed - a timeout, a Ctrl-C, a CI cancel - must not
    # leave mutated source on disk. The per-probe `finally` covers an
    # exception; it does not cover SIGTERM, and a half-applied probe left in a
    # working tree is a booby trap that looks like a real edit. It has already
    # happened once here: `if len(password) < MIN` was found sitting in the
    # tree as `if False` after a killed run.
    _in_flight: dict[Path, Path] = {}

    def _restore_all(*_args):
        for target, backup in list(_in_flight.items()):
            try:
                shutil.copy2(backup, target)
            except OSError:
                print(f"WARNING: could not restore {target} from {backup}", file=sys.stderr)
        _in_flight.clear()

    atexit.register(_restore_all)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, lambda *a: (_restore_all(), sys.exit(130)))
        except (ValueError, OSError):
            pass  # not the main thread, or the platform lacks it

    with tempfile.TemporaryDirectory() as backup_dir:
        for index, probe in enumerate(probes, 1):
            name = probe.get("name") or f"probe {index}"
            path = REPO / probe["file"]
            expect = probe.get("expect", "killed")
            backup = Path(backup_dir) / f"{index}-{path.name}"
            shutil.copy2(path, backup)
            _in_flight[path] = backup

            try:
                _apply(path, probe["old"], probe["new"])
            except (ValueError, KeyError) as exc:
                # Loudly, and as a failure. This is the case the tool exists for.
                print(f"  DID NOT LAND  {name}: {exc}")
                failures += 1
                shutil.copy2(backup, path)
                _in_flight.pop(path, None)
                results.append((name, "did-not-land"))
                continue

            try:
                completed = subprocess.run(
                    [*_pytest(), *probe["tests"]], cwd=REPO,
                    capture_output=True, text=True, timeout=probe.get("timeout", 600),
                )
                outcome = "killed" if completed.returncode != 0 else "survived"
            except subprocess.TimeoutExpired:
                outcome = "timeout"
            finally:
                shutil.copy2(backup, path)
                _in_flight.pop(path, None)

            ok = outcome == expect
            failures += 0 if ok else 1
            print(f"  {'ok   ' if ok else 'FAIL '} {outcome:9} (want {expect:9}) {name}")
            results.append((name, outcome))

    print(f"\n{len(results) - failures}/{len(results)} probes as expected")
    if failures:
        print("A probe that did not land is not a probe that passed.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
