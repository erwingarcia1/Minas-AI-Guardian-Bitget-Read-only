"""#77: the image shipped no scripts/, so v1.4.1's release note promised a
reading the deployment could not take. The fix is per-file COPY lines plus
matching .dockerignore re-includes — two lists in two files that mean the
same thing, which is this repo's recurring one-directional-containment
shape: each direction gets its own assertion, because a COPY without its
re-include fails the build loudly, while a re-include without its COPY is
dead config that reads as coverage.

Honest limit: these are registration guards. The execution guard — the
file actually present in a built image — needs `docker build`, which CI
does not run; the deploy's own `validate-config` step exercises the image,
and the boundary reading itself is the proof on the VM.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent


def _copied_scripts() -> set[str]:
    text = (ROOT / "Dockerfile").read_text()
    return set(re.findall(r"^COPY (scripts/\S+) ", text, re.M))


def _reincluded_scripts() -> set[str]:
    text = (ROOT / ".dockerignore").read_text()
    return set(re.findall(r"^!(scripts/\S+)$", text, re.M))


def test_the_boundary_report_ships_in_the_image():
    assert "scripts/retention-boundary.py" in _copied_scripts()


def test_every_copied_script_is_readmitted_past_dockerignore():
    """scripts/ is excluded wholesale, so a COPY without its `!` line fails
    the build only at build time — on the server, mid-deploy. Catch it here."""
    assert _copied_scripts() <= _reincluded_scripts()


def test_every_readmitted_script_is_actually_copied():
    """The quieter direction: a stale `!` line after a script is renamed
    keeps looking like coverage while shipping nothing."""
    assert _reincluded_scripts() <= _copied_scripts()


def test_every_copied_script_exists():
    for script in _copied_scripts():
        assert (ROOT / script).is_file(), f"{script} is copied but does not exist"


# ---------------------------------------------------------------------------
# deploy.sh deploys a TAG, not a branch tip.
#
# The defect these pin: the old deploy.sh pulled main and handed HEAD to the
# gate, and the workflow commits a log handoff after every tag — so main's
# tip was routinely one commit past the release and the gate (correctly)
# refused it. It blocked the v1.4.1 and v1.5.0 deploys; v1.5.0 was completed
# by hand, which is the error-prone thing the script exists to prevent.
#
# Run against throwaway clones in tmp_path, per test_release_process.py's
# reasoning. `docker` and `id` are stubbed on PATH so the script's git
# decisions run for real while the container half is scripted: the stub
# reports whatever version the test seeds, so the downgrade guard and the
# post-switchover assert are exercised without a daemon. The refusals are
# the point — a gate is only worth what its refusals are proven to do.

DEPLOY = ROOT / "scripts" / "deploy.sh"
DEPLOY_GATE = ROOT / "scripts" / "deploy-gate.sh"

_DOCKER_STUB = """\
#!/usr/bin/env bash
# Test stub. The version reports are read from $DOCKER_STUB_DIR so the test
# controls what "the container" claims to be running. A missing `machine`
# file plays a pre---machine image: argparse would exit 2 with nothing on
# stdout, which is what deploy.sh's fallback keys on.
case "$*" in
  "compose exec -T sentinel sentinel version --machine") cat "$DOCKER_STUB_DIR/machine" 2>/dev/null || exit 2 ;;
  "compose exec -T sentinel sentinel version") cat "$DOCKER_STUB_DIR/version" 2>/dev/null || exit 1 ;;
  "compose ps -q sentinel") echo stubcid ;;
  inspect*) echo "running healthy" ;;
  *) exit 0 ;;
esac
"""


def _run_git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
         "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True)


@pytest.fixture
def server(tmp_path):
    """A server-shaped clone: an origin holding two releases and an untagged
    tip (the post-tag handoff commit that caused the defect), stub `docker`
    and `id` (uid 0, so the script does not re-exec under real sudo)."""
    origin = tmp_path / "origin"
    (origin / "scripts").mkdir(parents=True)
    for script in (DEPLOY, DEPLOY_GATE):
        shutil.copy(script, origin / "scripts" / script.name)
        (origin / "scripts" / script.name).chmod(0o755)
    _run_git(origin, "init", "-q", "-b", "main")
    _run_git(origin, "add", "-A")
    _run_git(origin, "commit", "-qm", "first release")
    _run_git(origin, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    (origin / "CHANGELOG.md").write_text("notes\n")
    _run_git(origin, "add", "-A")
    _run_git(origin, "commit", "-qm", "second release")
    _run_git(origin, "tag", "-a", "v1.1.0", "-m", "v1.1.0")
    (origin / "log.md").write_text("handoff\n")
    _run_git(origin, "add", "-A")
    _run_git(origin, "commit", "-qm", "log handoff AFTER the tag")

    clone = tmp_path / "server"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)],
                   check=True, capture_output=True)

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (stubs / "docker").write_text(_DOCKER_STUB)
    (stubs / "docker").chmod(0o755)
    (stubs / "id").write_text("#!/usr/bin/env bash\necho 0\n")
    (stubs / "id").chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{stubs}:{env['PATH']}"
    env["DOCKER_STUB_DIR"] = str(state)
    return SimpleNamespace(repo=clone, env=env, state=state)


def _container_reports(server, version, tag, machine=True):
    """machine=False plays an image built before `sentinel version --machine`
    existed — a rollback target — whose human output is all there is."""
    (server.state / "version").write_text(
        f"Minas AI Guardian {version}\n  commit   abcdef0\n"
        f"  tag      {tag}\n  source   build-arg\n")
    if machine:
        (server.state / "machine").write_text(
            f"version={version}\ncommit=abcdef0\ntag={tag}\nsource=build-arg\n")
    else:
        (server.state / "machine").unlink(missing_ok=True)


def _deploy(server, *args):
    # start_new_session: no controlling terminal, so the downgrade guard's
    # /dev/tty read deterministically has nothing to read from.
    return subprocess.run(
        [str(server.repo / "scripts" / "deploy.sh"), *args],
        capture_output=True, text=True, env=server.env,
        cwd=str(server.repo), timeout=120, start_new_session=True)


def _head_tag(server):
    r = subprocess.run(
        ["git", "-C", str(server.repo), "describe", "--exact-match", "--tags", "HEAD"],
        capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def test_no_argument_deploys_the_latest_release_not_the_branch_tip(server):
    """The defect itself: HEAD starts at the untagged handoff commit, and the
    deploy must land on v1.1.0 anyway — resolved by version, checked out
    detached, gate passed, version asserted."""
    assert _head_tag(server) is None  # precondition: the tip is not a release
    _container_reports(server, "1.1.0", "v1.1.0")
    result = _deploy(server)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "target is v1.1.0 (latest release)" in result.stdout
    assert "is live and verified" in result.stdout
    assert _head_tag(server) == "v1.1.0"


def test_a_specific_tag_can_be_requested(server):
    _container_reports(server, "1.0.0", "v1.0.0")
    result = _deploy(server, "v1.0.0")
    # 1.0.0 == 1.0.0 running: same version, not a downgrade, no confirmation.
    assert result.returncode == 0, result.stderr + result.stdout
    assert _head_tag(server) == "v1.0.0"


def test_a_dirty_tree_is_refused_naming_the_files(server):
    (server.repo / "log.md").write_text("edited on the box\n")
    before = subprocess.run(["git", "-C", str(server.repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout
    result = _deploy(server)
    assert result.returncode != 0
    assert "log.md" in result.stderr
    assert "never edit on the server" in result.stderr
    after = subprocess.run(["git", "-C", str(server.repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout
    assert before == after  # refused before touching the checkout


def test_a_tag_that_does_not_exist_is_refused(server):
    result = _deploy(server, "v9.9.9")
    assert result.returncode != 0
    assert "does not exist" in result.stderr
    assert "v1.1.0" in result.stderr  # the refusal lists what does


def test_a_malformed_version_is_refused(server):
    result = _deploy(server, "latest")
    assert result.returncode != 0
    assert "not a release tag" in result.stderr


def test_a_downgrade_without_a_terminal_is_refused(server):
    _container_reports(server, "1.1.0", "v1.1.0")
    result = _deploy(server, "v1.0.0")
    assert result.returncode != 0
    assert "downgrade" in result.stderr
    # And nothing was switched over: the refusal must precede the checkout.
    assert _head_tag(server) is None


def test_the_wrong_image_after_switchover_fails_the_deploy(server):
    """Requirement: assert rather than report. The stub keeps claiming
    v1.0.0 after a v1.1.0 deploy — the script must exit non-zero."""
    _container_reports(server, "1.0.0", "v1.0.0")
    result = _deploy(server, "v1.1.0")
    assert result.returncode != 0
    assert "wrong image" in result.stderr


def test_the_gate_still_refuses_an_untagged_head(server):
    """deploy.sh now always hands the gate a tag, but the gate is the
    enforcement for the manual path too and must keep refusing on its own."""
    result = subprocess.run(
        [str(server.repo / "scripts" / "deploy-gate.sh")],
        capture_output=True, text=True, cwd=str(server.repo), timeout=30)
    assert result.returncode != 0
    assert "REFUSED" in result.stderr


def test_a_pre_machine_image_is_parsed_by_its_frozen_human_format(server):
    """A rollback target predates `sentinel version --machine`. Its image is
    already built, so its human format can never change — deploy.sh falls
    back to parsing it, and the deploy still verifies rather than failing
    with a false 'wrong image'."""
    _container_reports(server, "1.0.0", "v1.0.0", machine=False)
    result = _deploy(server, "v1.0.0")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "is live and verified" in result.stdout
