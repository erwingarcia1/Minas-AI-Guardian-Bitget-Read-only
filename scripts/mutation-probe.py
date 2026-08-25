#!/usr/bin/env python3
"""Hand-written mutation probes that cannot silently fail to apply.

    scripts/mutation-probe.py probes.json [--workers N|auto]

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

## Workers (#96)

With `--workers N` (or `auto`: one per core, at most 8) the probes fan out
across N private copies of the tree, one per worker, and the working tree is
never patched at all. `xargs -P` over this script would be wrong by
construction - two probes sharing a tree corrupt each other's patches - so
each worker gets a complete copy (tracked and untracked files, from `git
ls-files`; not `.venv`, not `mutants/`) and patches, tests and restores only
inside it. The tests run with the copy as cwd and first on `PYTHONPATH`, and
each worker proves, before it touches a probe, that `import sentinel` resolves
INSIDE its copy. If it resolved to the real tree, every probe in that worker
would test unmutated code and report `survived` - or worse, `killed` for the
wrong reason - so a worker that fails that check fails the whole run, the
same way a probe that did not land does.

`--workers 1` (the default) is the original in-place path, byte for byte:
patch the working tree, run, restore, with the lock and the signal handlers
that protect it. The lock is taken in both modes - a copy taken while an
in-place run has a mutation applied would carry that mutant into every
worker.
"""
from __future__ import annotations

import atexit
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
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


def _python() -> str:
    venv = REPO / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _pytest() -> list[str]:
    return [_python(), "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def _worker_env(root: Path) -> dict[str, str]:
    """The copy first on PYTHONPATH, so `import sentinel` is the copy's."""
    env = dict(os.environ)
    prior = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(root) if not prior else f"{root}{os.pathsep}{prior}"
    return env


def _tree_files() -> list[str]:
    """Everything a test run needs and nothing it must not share: tracked plus
    untracked-but-not-ignored files. `.venv`, `mutants/`, logs and caches are
    ignored, so they stay out; the interpreter comes from the real tree."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True,
    ).stdout
    return [name for name in out.decode().split("\0") if name]


def _copy_tree(files: list[str], root: Path) -> None:
    for name in files:
        source = REPO / name
        if not source.is_file():
            continue  # deleted in the working tree but still in the index
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _prove_isolation(root: Path) -> None:
    """Raises unless `import sentinel` in this copy resolves inside it."""
    completed = subprocess.run(
        [_python(), "-c", "import sentinel, sys; sys.stdout.write(sentinel.__file__)"],
        cwd=root, env=_worker_env(root), capture_output=True, text=True, timeout=60, check=False,
    )
    resolved = completed.stdout.strip()
    if completed.returncode != 0 or not resolved.startswith(str(root.resolve()) + os.sep):
        raise RuntimeError(
            f"worker copy at {root} imports sentinel from {resolved or completed.stderr.strip()!r}, "
            f"not from itself - its probes would test the wrong tree")


def _resolve_workers(spec: str) -> int:
    if spec == "auto":
        return max(1, min(8, os.cpu_count() or 1))
    workers = int(spec)
    if workers < 1:
        raise ValueError("--workers must be at least 1")
    return workers


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


USAGE = "usage: scripts/mutation-probe.py <probes.json> [--workers N|auto]"


def _parse(argv: list[str]) -> tuple[Path, int]:
    args = list(argv[1:])
    workers = "1"
    if "--workers" in args:
        at = args.index("--workers")
        try:
            workers = args[at + 1]
        except IndexError:
            raise ValueError("--workers needs a value") from None
        del args[at:at + 2]
    if len(args) != 1:
        raise ValueError(USAGE)
    return Path(args[0]), _resolve_workers(workers)


def _run_probe(root: Path, index: int, probe: dict) -> tuple[str, str, str]:
    """One probe inside `root`, restored afterwards. Returns (name, outcome, line)."""
    name = probe.get("name") or f"probe {index}"
    expect = probe.get("expect", "killed")
    path = root / probe["file"]
    try:
        original = _apply(path, probe["old"], probe["new"])
    except (ValueError, KeyError, OSError) as exc:
        return name, "did-not-land", f"  DID NOT LAND  {name}: {exc}"
    try:
        completed = subprocess.run(
            [*_pytest(), *probe["tests"]], cwd=root, env=_worker_env(root),
            capture_output=True, text=True, timeout=probe.get("timeout", 600), check=False,
        )
        outcome = "killed" if completed.returncode != 0 else "survived"
    except subprocess.TimeoutExpired:
        outcome = "timeout"
    finally:
        path.write_text(original)
    ok = outcome == expect
    return name, outcome, f"  {'ok   ' if ok else 'FAIL '} {outcome:9} (want {expect:9}) {name}"


def _run_parallel(probes: list[dict], workers: int) -> tuple[list[tuple[str, str]], int]:
    """Probes fanned across `workers` private copies of the tree. Output lines
    are printed as probes finish (attributable by name, as before); the
    returned results are in probe order."""
    files = _tree_files()
    queue = list(enumerate(probes, 1))
    queue_lock = threading.Lock()
    print_lock = threading.Lock()
    slots: list[tuple[int, str, str] | None] = [None] * len(probes)

    def worker(number: int, base: Path) -> None:
        root = base / f"worker-{number}"
        root.mkdir()
        _copy_tree(files, root)
        _prove_isolation(root)
        while True:
            with queue_lock:
                if not queue:
                    return
                index, probe = queue.pop(0)
            name, outcome, line = _run_probe(root, index, probe)
            with print_lock:
                print(line, flush=True)
            slots[index - 1] = (index, name, outcome)

    with tempfile.TemporaryDirectory(prefix="mutation-probe-") as base, \
            ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, n, Path(base)) for n in range(1, workers + 1)]
        errors = [f.exception() for f in futures if f.exception() is not None]
    if errors:
        for error in errors:
            print(f"  WORKER FAILED  {error}", file=sys.stderr)
        # Whatever the finished probes said, a worker that could not prove its
        # isolation - or died mid-queue - leaves probes unanswered.
        unanswered = [slot is None for slot in slots].count(True)
        print(f"  {unanswered} probe(s) never ran", file=sys.stderr)
    results = [(name, outcome) for slot in slots if slot is not None for _, name, outcome in [slot]]
    failures = sum(1 for index, probe in enumerate(probes, 1)
                   if slots[index - 1] is None
                   or slots[index - 1][2] != probe.get("expect", "killed"))
    return results, failures


def main(argv: list[str]) -> int:
    try:
        probes_file, workers = _parse(argv)
    except ValueError as exc:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        print(exc if str(exc) != USAGE else USAGE, file=sys.stderr)
        return 2

    lock = _take_the_lock()
    if lock is None:
        print(f"another probe run holds {LOCKFILE.name} - it is patching these same "
              f"files, and two runs at once leave mutations behind. Try again after "
              f"it finishes.", file=sys.stderr)
        return 2

    probes = json.loads(probes_file.read_text())
    results, failures = [], 0

    if workers > 1:
        print(f"{len(probes)} probes across {workers} workers, each in its own copy of the tree")
        results, failures = _run_parallel(probes, workers)
        print(f"\n{len(probes) - failures}/{len(probes)} probes as expected")
        if failures:
            print("A probe that did not land is not a probe that passed.", file=sys.stderr)
        return 1 if failures else 0

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
                    capture_output=True, text=True, timeout=probe.get("timeout", 600), check=False,
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
