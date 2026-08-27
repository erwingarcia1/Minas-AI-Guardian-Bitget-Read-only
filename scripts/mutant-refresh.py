#!/usr/bin/env python3
"""Incremental regeneration of mutants/, validated per function (#97 follow-up).

    scripts/mutant-refresh.py plan     <old-manifest> <current-manifest>
    scripts/mutant-refresh.py validate <current-manifest>

The from-scratch path costs hours (generation of the whole package,
single-threaded); a changed file regenerates in seconds and a full stats
re-collection in under a minute (measured 2026-08-26: 9 s for sms.py, 27 s
for stats, 10 s for a no-op). mutmut already regenerates per file and resets
verdicts per changed function - but it decides "changed" by MTIME, and a
restored archive or a checkout can leave a changed source OLDER than its
mutant copy, which mutmut reads as unmodified. That is the exact shape of
the 2026-08-23 hollow pass: a changed detector judged against its old code.

So this never trusts mtimes. `plan` diffs the recorded source manifest
against the current one and deletes the copy, .meta and .spans of every
changed, added or removed file, so mutmut MUST regenerate them (a missing
copy is copied fresh and mutated; a removed file's stale map entries are
dropped by mutmut's own cleanup). It also deletes mutmut-stats.json so the
test-to-function map is re-collected from scratch: a new or changed
function with no map entry would score "no tests", and that is cheap to
avoid. `validate` then runs AFTER regeneration and proves the result: for
every file in the current manifest, a .meta must exist and every function
hash it records must equal the hash computed from the CURRENT source with
mutmut's own function. Anything else is a mismatch, and a mismatch means
the caller rebuilds from scratch. Unverifiable is stale; fail closed.

`plan` refuses (exit 3) rather than plan when the state cannot be mapped:
no old manifest, an old manifest whose format it does not understand, or an
UNCHANGED file whose .meta is missing (its verdicts cannot be trusted and
its regeneration cannot be proven). Those go to the full rebuild.

Exit codes: 0 ok; 1 validation failed; 2 usage; 3 cannot plan incrementally.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MUTANTS = REPO / "mutants"


def mutated_patterns(setup_cfg: Path | None = None) -> list[str]:
    """The `only_mutate` globs from the gate's setup.cfg: files mutmut
    mutates. Everything else under source_paths is copied plain. Empty means
    every file is mutated."""
    import configparser

    path = setup_cfg or (Path.cwd() / "setup.cfg")
    if not path.exists():
        return []
    cfg = configparser.ConfigParser()
    cfg.read(path)
    if not cfg.has_option("mutmut", "only_mutate"):
        return []
    return [line.strip() for line in cfg.get("mutmut", "only_mutate").splitlines() if line.strip()]


def is_mutated(source: str, patterns: list[str]) -> bool:
    import fnmatch

    return not patterns or any(fnmatch.fnmatch(source, pattern) for pattern in patterns)


def read_manifest(path: Path) -> dict[str, str]:
    """`sha256sum` output -> {source path: hash}. Raises on any line it cannot read."""
    entries: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"unreadable manifest line: {raw!r}")
        entries[parts[1].strip()] = parts[0]
    return entries


def plain_files(repo: Path = REPO) -> list[str]:
    """Every non-.py file under sentinel/ that mutmut copies plain - the
    source manifest tracks .py only, and a stale plain copy (schema.sql,
    2026-08-27: the tests inside mutants/ built databases without a new
    table and the gate measured nothing) is the same hollow shape as a
    stale mutant. Excludes caches."""
    root = repo / "sentinel"
    out = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix != ".py" and "__pycache__" not in path.parts:
            out.append(str(path.relative_to(repo)))
    return out


def stale_plain_files(mutants: Path = MUTANTS, repo: Path = REPO) -> list[str]:
    return [f for f in plain_files(repo)
            if not (mutants / f).exists() or (mutants / f).read_bytes() != (repo / f).read_bytes()]


def _artifacts(source: str) -> list[Path]:
    copy = MUTANTS / source
    return [copy, copy.with_name(copy.name + ".meta"), copy.with_name(copy.name + ".spans")]


def plan(old: dict[str, str], current: dict[str, str], mutants: Path = MUTANTS,
         patterns: list[str] | None = None) -> dict[str, list[str]]:
    """What must be regenerated. Raises RuntimeError when the state cannot be
    mapped and the caller must rebuild from scratch."""
    patterns = mutated_patterns() if patterns is None else patterns
    changed = sorted(p for p in current if p in old and old[p] != current[p])
    added = sorted(p for p in current if p not in old)
    removed = sorted(p for p in old if p not in current)
    unchanged = sorted(p for p in current if p in old and old[p] == current[p])
    for source in unchanged:
        copy = mutants / source
        if not copy.exists():
            raise RuntimeError(f"{source} is recorded unchanged but its copy is missing - "
                               f"it cannot be proven regenerated")
        if is_mutated(source, patterns) and not copy.with_name(copy.name + ".meta").exists():
            raise RuntimeError(f"{source} is recorded unchanged but its .meta is missing - "
                               f"its verdicts cannot be trusted and it cannot be proven regenerated")
    return {"changed": changed, "added": added, "removed": removed, "unchanged": unchanged}


def apply(planned: dict[str, list[str]], mutants: Path = MUTANTS, repo: Path = REPO) -> None:
    # Plain (non-.py) copies that differ are deleted too, so mutmut's copy
    # step - which skips anything already present - copies them fresh.
    for plain in stale_plain_files(mutants, repo):
        copy = mutants / plain
        if copy.exists():
            copy.unlink()
    for source in planned["changed"] + planned["added"] + planned["removed"]:
        copy = mutants / source
        for artifact in (copy, copy.with_name(copy.name + ".meta"), copy.with_name(copy.name + ".spans")):
            if artifact.is_dir():
                shutil.rmtree(artifact)
            elif artifact.exists():
                artifact.unlink()
    stats = mutants / "mutmut-stats.json"
    if stats.exists():
        stats.unlink()


def validate(current: dict[str, str], mutants: Path = MUTANTS, repo: Path = REPO,
             patterns: list[str] | None = None) -> list[str]:
    """Every file proves itself: a mutated file by every recorded function
    hash matching the current source, a plain copy by byte equality. Returns
    the problems; empty means proven."""
    patterns = mutated_patterns() if patterns is None else patterns
    problems: list[str] = []
    compute_function_hashes = None  # imported when the first mutated file needs it
    for plain in stale_plain_files(mutants, repo):
        problems.append(f"{plain}: plain (non-.py) copy missing or differs from the source - "
                        f"the tests inside mutants/ would run against other data")
    for source in sorted(current):
        copy = mutants / source
        meta = copy.with_name(copy.name + ".meta")
        if not copy.exists():
            problems.append(f"{source}: no mutant copy after regeneration")
            continue
        if not is_mutated(source, patterns):
            if copy.read_bytes() != (repo / source).read_bytes():
                problems.append(f"{source}: plain copy differs from the current source - "
                                f"the tests would run against other code")
            continue
        if not meta.exists():
            problems.append(f"{source}: no .meta after regeneration")
            continue
        try:
            recorded = json.loads(meta.read_text()).get("hash_by_function_name", {})
        except (ValueError, OSError) as exc:
            problems.append(f"{source}: unreadable .meta ({exc})")
            continue
        if compute_function_hashes is None:
            # mutmut's own hash, so "matches" means what mutmut means by it.
            # Imported here, not at the top: plain copies validate without
            # mutmut installed (CI's test job has none; the gate job does).
            from mutmut.mutation.file_mutation import compute_function_hashes
        actual = compute_function_hashes((repo / source).read_text())
        for func, digest in sorted(recorded.items()):
            if actual.get(func) != digest:
                problems.append(f"{source}: {func} recorded {digest}, current source hashes "
                                f"{actual.get(func, 'ABSENT')} - the mutants are of other code")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "plan" and len(argv) == 4:
        old_path, current_path = Path(argv[2]), Path(argv[3])
        if not old_path.exists():
            print("mutant-refresh: no recorded manifest - nothing to diff against", file=sys.stderr)
            return 3
        try:
            old, current = read_manifest(old_path), read_manifest(current_path)
            planned = plan(old, current)
        except (ValueError, RuntimeError) as exc:
            print(f"mutant-refresh: cannot plan incrementally: {exc}", file=sys.stderr)
            return 3
        stale_plain = stale_plain_files()
        apply(planned)
        if stale_plain:
            print(f"mutant-refresh: refreshing {len(stale_plain)} plain (non-.py) file(s): {', '.join(stale_plain)}")
        print(f"mutant-refresh: regenerating {len(planned['changed'])} changed + {len(planned['added'])} added, "
              f"dropping {len(planned['removed'])} removed, keeping {len(planned['unchanged'])} unchanged; "
              f"test map re-collected")
        for kind in ("changed", "added", "removed"):
            for source in planned[kind]:
                print(f"  {kind:8s} {source}")
        return 0
    if len(argv) == 3 and argv[1] == "validate":
        try:
            current = read_manifest(Path(argv[2]))
        except ValueError as exc:
            print(f"mutant-refresh: {exc}", file=sys.stderr)
            return 1
        problems = validate(current)
        if problems:
            print("mutant-refresh: regeneration did NOT land - rebuilding from scratch:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print(f"mutant-refresh: validated {len(current)} files - every recorded function hash matches the current source")
        return 0
    print("usage: mutant-refresh.py plan <old-manifest> <current-manifest> | validate <current-manifest>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
