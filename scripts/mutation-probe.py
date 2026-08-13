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

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


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

    probes = json.loads(Path(argv[1]).read_text())
    results, failures = [], 0

    with tempfile.TemporaryDirectory() as backup_dir:
        for index, probe in enumerate(probes, 1):
            name = probe.get("name") or f"probe {index}"
            path = REPO / probe["file"]
            expect = probe.get("expect", "killed")
            backup = Path(backup_dir) / f"{index}-{path.name}"
            shutil.copy2(path, backup)

            try:
                _apply(path, probe["old"], probe["new"])
            except (ValueError, KeyError) as exc:
                # Loudly, and as a failure. This is the case the tool exists for.
                print(f"  DID NOT LAND  {name}: {exc}")
                failures += 1
                shutil.copy2(backup, path)
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
