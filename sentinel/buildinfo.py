"""What is actually running: version, and the commit it was built from.

Until this existed, the only way to know what was deployed was to trust that
the deploy worked - the same assume-rather-than-verify shape this codebase
keeps finding in its own detectors (a poll that stamped success before doing
the work, a migration whose failure looked like a no-op). A version number the
operator can read off the running process is the verification.

The image has no `.git`: the Dockerfile copies `sentinel/`, `config/` and
`pyproject.toml` and nothing else, deliberately. So the commit is passed in as
a build argument by the deploy (`scripts/deploy-gate.sh --export` prints it,
docker-compose.yml forwards it) and lands in the environment.

Nothing here guesses. When the commit was not supplied, this says `unknown`
rather than reaching for something plausible - a build-info line that quietly
reports the wrong commit is worse than one that admits it does not know, in
exactly the way an unrun gate reporting "pass" is worse than one reporting
"skipped".
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache

from sentinel import __version__

UNKNOWN = "unknown"


def _from_git(*args: str) -> str | None:
    """Ask git, if there is a git to ask. Source checkouts only."""
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


@lru_cache(maxsize=1)
def build_info() -> dict[str, str]:
    """`{version, commit, tag, source}` - never raises, never invents.

    `source` says where the commit came from, because "which commit" and "how
    do you know" are different questions and the second one is the one that
    tells you whether to believe the first:

      `build-arg`  baked in at image build time - what production reports
      `git`        read from a checkout at runtime - local/source runs
      `unknown`    not supplied and no checkout to ask; reported as such
    """
    commit = os.environ.get("SENTINEL_GIT_SHA", "").strip()
    tag = os.environ.get("SENTINEL_GIT_TAG", "").strip()
    source = "build-arg"

    if not commit:
        commit = _from_git("rev-parse", "HEAD") or ""
        tag = tag or _from_git("describe", "--exact-match", "--tags", "HEAD") or ""
        source = "git" if commit else UNKNOWN

    return {
        "version": __version__,
        "commit": commit or UNKNOWN,
        "tag": tag or UNKNOWN,
        "source": source,
    }


def short_commit() -> str:
    commit = build_info()["commit"]
    return commit[:12] if commit != UNKNOWN else UNKNOWN


def version_line() -> str:
    """One line, for a dashboard footer or a log at startup."""
    info = build_info()
    tag = info["tag"]
    suffix = "" if tag == UNKNOWN or tag == f"v{info['version']}" else f" (tag {tag})"
    return f"v{info['version']} · {short_commit()}{suffix}"
