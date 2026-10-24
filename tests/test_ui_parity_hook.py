"""The ui-parity hook and skill (decision 25, 2026-10-24): a change to a
user-facing surface is told where its counterparts live, every time.

The owner's rule - "when a change is made to the UI, the change should
reflect in both web UIs and the mobile UIs when necessary" - is held by three
things: the skill (.claude/skills/ui-parity/SKILL.md) that maps each surface
to its counterparts, its guards and its records; the PostToolUse hook
(scripts/ui-parity-reminder.py, registered in .claude/settings.json) that
tells the session to invoke the skill after a surface was written; and the
parity tests the skill names. These tests drive the hook as the harness does,
a subprocess fed JSON on stdin, because a hook that merely imports cleanly can
still print the wrong shape, exit non-zero and turn every tool call into an
error, or fire on a `grep`. And they hold the hook's map and the skill's map
to each other: a surface the hook knows and the skill does not name is a
reminder that points nowhere.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ui-parity-reminder.py"
SETTINGS = REPO / ".claude" / "settings.json"
SKILL = REPO / ".claude" / "skills" / "ui-parity" / "SKILL.md"


def _module():
    """scripts/ is not a package: the hook is imported by path, for SURFACES."""
    spec = importlib.util.spec_from_file_location("ui_parity_reminder", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SURFACES = _module().SURFACES


def _run(payload, *, cwd: Path = REPO, project_dir: str | None = None) -> subprocess.CompletedProcess:
    """The hook as .claude/settings.json runs it: `python3 -I <script>`, so a
    json.py or shlex.py dropped into scripts/ cannot stand in for the stdlib."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    env = {name: value for name, value in os.environ.items() if name != "CLAUDE_PROJECT_DIR"}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = project_dir
    return subprocess.run([sys.executable, "-I", str(SCRIPT)], input=raw, capture_output=True, text=True,
                          cwd=cwd, env=env, timeout=30)


def _context(result: subprocess.CompletedProcess) -> str:
    """The reminder, after checking the output is exactly the hook's shape."""
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr
    output = json.loads(result.stdout)
    assert set(output) == {"hookSpecificOutput"}
    inner = output["hookSpecificOutput"]
    assert set(inner) == {"hookEventName", "additionalContext"}
    assert inner["hookEventName"] == "PostToolUse"
    return inner["additionalContext"]


def _silent(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout
    assert result.stderr == "", result.stderr


def _edit(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}


def _bash(command: str, stdout: str = "") -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False}}


# --- the hook, as the harness runs it --------------------------------------


def test_a_change_to_the_portal_is_told_about_the_app_and_the_skill():
    context = _context(_run(_edit(str(REPO / "sentinel" / "portal.py")), project_dir=str(REPO)))
    assert "sentinel/portal.py is the member portal" in context
    assert "mobile/src/app/" in context, "the counterpart surface is not named"
    assert "`ui-parity` skill (.claude/skills/ui-parity/SKILL.md)" in context
    assert "before finishing" in context.lower()
    assert "\n" not in context, "one paragraph: the harness shows it inline"


@pytest.mark.parametrize("path, counterpart", [
    ("sentinel/dashboard.py", "no app counterpart"),
    ("sentinel/brand.py", "mobile/src/theme/tokens.json"),
    ("sentinel/static/minas-mark.png", "design/brand/"),
    ("sentinel/api.py", "api/openapi.yaml"),
    ("api/openapi.yaml", "npm run gen:api"),
    ("sentinel/alerts/base.py", "mobile/src/app/alert/[id].tsx"),
    ("design/portal/landing.html", "_PORTAL_STYLE"),
    ("design/dashboard/DESIGN-SYSTEM.md", "sentinel/dashboard.py"),
    ("design/brand/minas-logo.png", "mobile/assets/images/"),
    ("mobile/src/app/landing.tsx", "_render_landing"),
    ("mobile/src/app/(tabs)/index.tsx", "_render_status"),
    ("mobile/src/theme/tokens.json", "_PORTAL_STYLE :root"),
    ("mobile/src/ui/index.tsx", ".badge"),
    ("mobile/src/auth/session.tsx", "_render_login"),
    ("mobile/src/api/schema.d.ts", "npm run gen:api"),
    ("mobile/src/config.ts", "sentinel/portal.py"),
    ("mobile/app.json", "design/brand/"),
    ("mobile/assets/images/icon.png", "design/brand/"),
])
def test_every_surface_names_its_counterpart(path, counterpart):
    context = _context(_run(_edit(str(REPO / path)), project_dir=str(REPO)))
    assert f"{path} is " in context, context
    assert counterpart in context, context


@pytest.mark.parametrize("tool, key", [
    ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path"), ("NotebookEdit", "file_path"),
])
def test_every_editing_tool_is_covered(tool, key):
    payload = {"tool_name": tool, "tool_input": {key: str(REPO / "mobile" / "app.json")}}
    assert "mobile/app.json is " in _context(_run(payload, project_dir=str(REPO)))


@pytest.mark.parametrize("path", [
    "sentinel/engine.py", "tests/test_portal.py", "docs/x.md", "scripts/release.sh", "CHANGELOG.md",
    "mobile/package.json", "mobile/CHANGELOG.md", "sentinel/portal_extras.py", "docs/design/notes.md",
])
def test_a_change_elsewhere_says_nothing(path):
    _silent(_run(_edit(str(REPO / path)), project_dir=str(REPO)))


def test_a_read_of_a_surface_is_not_a_change():
    _silent(_run({"tool_name": "Read", "tool_input": {"file_path": str(REPO / "sentinel" / "portal.py")}}))
    _silent(_run({"tool_name": "Grep", "tool_input": {"pattern": "landing", "path": str(REPO / "mobile" / "src")}}))


@pytest.mark.parametrize("command, written", [
    ("sed -i 's/Sign in/Sign in now/' sentinel/portal.py", "sentinel/portal.py"),
    ("sed -i.bak -e 's/a/b/' sentinel/portal.py", "sentinel/portal.py"),
    ("sed -Ei 's/a/b/' mobile/src/app/landing.tsx", "mobile/src/app/landing.tsx"),
    ("sed --in-place 's/a/b/' sentinel/dashboard.py", "sentinel/dashboard.py"),
    ("sudo sed -i 's/a/b/' sentinel/brand.py", "sentinel/brand.py"),
    ("cat > mobile/src/app/landing.tsx <<'EOF'\nexport default function Landing() {}\nEOF", "mobile/src/app/landing.tsx"),
    ("echo '# note' >> api/openapi.yaml", "api/openapi.yaml"),
    ("printf 'x' | tee design/portal/landing.html", "design/portal/landing.html"),
    ("printf 'x' | tee -a sentinel/static/minas-mark.png >/dev/null", "sentinel/static/minas-mark.png"),
    ("cp /tmp/mark.png sentinel/static/minas-mark.png", "sentinel/static/minas-mark.png"),
    ("mv /tmp/app.json mobile/app.json", "mobile/app.json"),
    ("cp -r /tmp/images/ mobile/assets/images/", "mobile/assets/images/"),
    ("python3 - <<'EOF'\nfrom pathlib import Path\nPath('sentinel/portal.py').write_text('x')\nEOF", "sentinel/portal.py"),
    (f"sed -i 's/a/b/' {REPO}/sentinel/alerts/base.py", "sentinel/alerts/base.py"),
])
def test_a_bash_command_that_writes_a_surface_is_reminded(command, written):
    context = _context(_run(_bash(command), project_dir=str(REPO)))
    assert f"{written} is " in context, context


@pytest.mark.parametrize("command", [
    "grep -n landing-title sentinel/portal.py",
    "grep -rn 'Sign in' sentinel/portal.py mobile/src/app/",
    "sed -n '1240,1260p' sentinel/portal.py",
    "cat sentinel/portal.py | head -50",
    "cat mobile/app.json",
    "cp sentinel/portal.py /tmp/portal-backup.py",
    "grep -n landing sentinel/portal.py > /tmp/hits.txt 2>&1",
    "wc -l mobile/src/app/landing.tsx design/portal/landing.html",
    "git diff sentinel/portal.py",
    "python3 -m pytest -q tests/test_portal.py tests/test_mobile_app.py",
    "ls -la sentinel/static/",
    "diff design/portal/styles.css /tmp/styles.css",
])
def test_a_bash_command_that_only_reads_a_surface_says_nothing(command):
    """The contract's line: `sed -i` is a change; `grep`, `cat`, `sed -n` and a
    copy FROM the file are not, however many UI paths they mention."""
    _silent(_run(_bash(command, stdout="sentinel/portal.py:1300:<h1 id=\"landing-title\">\n"), project_dir=str(REPO)))


def test_a_read_whose_output_mentions_a_surface_is_still_a_read():
    output = "sentinel/portal.py:1300:Sign in\nmobile/src/app/sign-in.tsx:40:Sign in\n"
    _silent(_run(_bash("grep -rn 'Sign in' sentinel mobile/src", stdout=output), project_dir=str(REPO)))


def test_a_write_whose_response_lists_a_changed_surface_is_reminded():
    """The harness may list what a command changed in tool_response; once the
    command is known to write, those paths count too."""
    output = "wrote sentinel/static/minas-mark.png\nwrote mobile/assets/images/icon.png\n"
    context = _context(_run(_bash("python3 scripts/brand-assets.py derive > /tmp/derive.log", stdout=output),
                            project_dir=str(REPO)))
    assert "sentinel/static/minas-mark.png is " in context
    assert "mobile/assets/images/icon.png is " in context


def test_a_command_that_writes_two_surfaces_names_each_surface_once():
    command = ("cp /tmp/a.png sentinel/static/minas-mark.png && cp /tmp/b.png mobile/assets/images/icon.png"
               " && cp /tmp/c.png mobile/assets/images/splash-icon.png")
    context = _context(_run(_bash(command), project_dir=str(REPO)))
    assert context.count("sentinel/static/minas-mark.png is ") == 1
    assert context.count("mobile/assets/images/icon.png is ") == 1
    assert "splash-icon" not in context, "one reminder per surface, not one per file"


@pytest.mark.parametrize("raw", [
    "", "   \n", "not json {", "[]", "null", "42", '"a string"',
    '{"tool_name": "Edit"}',
    '{"tool_name": "Edit", "tool_input": {"file_path": 123}}',
    '{"tool_name": "Edit", "tool_input": "sentinel/portal.py"}',
    '{"tool_name": "Bash", "tool_input": {"command": null}}',
    '{"tool_input": {"file_path": "sentinel/portal.py"}}',
])
def test_garbage_and_empty_input_exit_quietly(raw):
    """A hook that fails turns the tool call it follows into an error; this
    one may be wrong about a surface, never loud about bad input."""
    _silent(_run(raw))


def test_an_absolute_path_under_another_root_still_matches_by_suffix(tmp_path):
    context = _context(_run(_edit("/srv/another-checkout/sentinel/portal.py"), cwd=tmp_path))
    assert "sentinel/portal.py is the member portal" in context


def test_a_known_root_is_stripped_from_the_path_the_reminder_names(tmp_path):
    payload = _edit("/srv/another-checkout/mobile/src/app/landing.tsx")
    payload["cwd"] = "/srv/another-checkout"
    assert "UI surface changed. mobile/src/app/landing.tsx is the app's screens" in _context(_run(payload, cwd=tmp_path))
    payload = _edit("/srv/elsewhere/mobile/src/app/landing.tsx")
    context = _context(_run(payload, cwd=tmp_path, project_dir="/srv/elsewhere"))
    assert "UI surface changed. mobile/src/app/landing.tsx is the app's screens" in context


# --- the registration: settings, skill and map ------------------------------


def test_the_settings_run_the_hook_after_every_edit_write_and_bash():
    settings = json.loads(SETTINGS.read_text())
    entries = settings["hooks"]["PostToolUse"]
    assert entries, "no PostToolUse hook is registered"
    commands = []
    for entry in entries:
        for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"):
            assert re.fullmatch(entry["matcher"], tool), f"{tool} is not matched by {entry['matcher']!r}"
        commands += [hook["command"] for hook in entry["hooks"] if hook.get("type") == "command"]
    ours = [command for command in commands if "scripts/ui-parity-reminder.py" in command]
    assert ours, f"no command runs the reminder: {commands}"
    assert all(command.startswith("python3 ") for command in ours), "the hook runs on the system python3"
    assert all(" -I " in command for command in ours), (
        "isolated mode: without -I the script's own directory, scripts/, leads sys.path, and a json.py or "
        "shlex.py placed there would be imported in place of the stdlib on every tool call")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_repos_gitignore_keeps_the_settings_file_and_drops_the_local_one(tmp_path):
    """.gitignore ignores settings.local.json (machine-local permissions) and
    must not swallow the project file with it, or the hook exists on one
    machine only. Judged in a scratch repository that carries only this
    repo's .gitignore, so a machine's own excludes cannot decide it: the
    owner's clone hides /.claude/ in .git/info/exclude because the worktrees
    live there, which is exactly the kind of rule this must not inherit."""
    (tmp_path / ".gitignore").write_text((REPO / ".gitignore").read_text())
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, env=env, check=True, capture_output=True, text=True, timeout=30)

    def ignored(path: str) -> bool:
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode in (0, 1), result.stderr
        return result.returncode == 0

    assert not ignored(".claude/settings.json"), "the repo's .gitignore drops the committed hook config"
    assert ignored(".claude/settings.local.json"), "the machine-local settings file is no longer ignored"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_this_checkout_lets_a_plain_git_add_carry_the_hook_config_and_the_skill():
    """The repo's .gitignore is not the only ignore rule a clone obeys. The
    owner's clone hides `/.claude/` in .git/info/exclude so the worktrees
    under it stay out of `git status`; every worktree shares that file, and a
    path it hides is left out of `git add -A` without a word. A commit made
    that way carries this test file and not its subjects, and CI goes red on
    FileNotFoundError while the records say the hook is committed. So this is
    judged in THIS checkout, not a scratch one: each of the two files is
    tracked already (an exclude rule has no hold on a tracked file), or a
    plain `git add` would take it. Red here names the `git add -f` to run
    before the commit; nothing else makes the files reach it."""

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=30)

    inside = git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("not a git checkout")
    for path in (".claude/settings.json", ".claude/skills/ui-parity/SKILL.md"):
        if git("ls-files", "--error-unmatch", "--", path).returncode == 0:
            continue
        ignored = git("check-ignore", "-q", "--", path)
        assert ignored.returncode in (0, 1), ignored.stderr
        assert ignored.returncode == 1, (
            f"{path} is not tracked and this checkout ignores it (`git check-ignore -v {path}` names the rule), "
            f"so `git add -A` leaves it out of the commit and the suite goes red in CI without it: "
            f"stage it with `git add -f {path}` before committing")


def _skill_parts() -> tuple[str, str]:
    text = SKILL.read_text()
    assert text.startswith("---\n"), "the skill has no frontmatter"
    front, body = text[4:].split("\n---\n", 1)
    return front, body


def test_the_skill_is_named_and_pushy_about_both_surfaces():
    front, _body = _skill_parts()
    assert re.search(r"^name:\s*ui-parity\s*$", front, re.M), front
    description = front.split("description:", 1)[1]
    assert "mobile" in description.lower() and "portal" in description.lower()
    assert "even when" in description, "the description must fire when the user names only one surface"


def test_the_skill_names_every_surface_the_hook_knows():
    """The registration pair: the hook's rows and the skill's map are the
    same list, or a reminder sends the session to a map with no such row."""
    _front, body = _skill_parts()
    missing = [known for known, _surface, _counterparts in SURFACES if known not in body]
    assert not missing, f"the hook reminds about surfaces the skill does not map: {missing}"


def test_the_skill_names_the_guards_and_the_records():
    _front, body = _skill_parts()
    for needle in (
        "tests/test_surface_styles.py", "tests/test_no_prevention_claims.py", "tests/test_mobile_app.py",
        "tests/test_portal_address.py", "tests/test_api_contract.py", "npm run gen:api", "npm run typecheck",
        "npm test -- --ci", "npx expo export", "scripts/mutation-probe.py scripts/probes/design-guards.json",
        "scripts/brand-assets.py derive", "CHANGELOG.md", "mobile/CHANGELOG.md", "docs/portal-design.md",
        "design/portal/DESIGN-SYSTEM.md", "design/dashboard/DESIGN-SYSTEM.md", "docs/DECISIONS-NEEDED.md",
        "when necessary", "21d", "21e",
    ):
        assert needle in body, f"the skill no longer names {needle!r}"


def test_the_skill_body_stays_short_enough_to_be_read_every_time():
    _front, body = _skill_parts()
    assert len(body.splitlines()) < 250


def test_every_surface_the_hook_knows_exists_in_the_tree():
    """A row for a path that is gone reminds about nothing and hides the
    rename it should have followed."""
    for known, _surface, _counterparts in SURFACES:
        target = REPO / known
        assert target.exists(), f"{known} is not in the tree"
        assert target.is_dir() if known.endswith("/") else target.is_file(), known
