#!/usr/bin/env python3
"""PostToolUse hook: a tool call that changed a user-facing surface is told
where the change's counterparts live (the owner's rule of 2026-10-24,
decision 25).

"When a change is made to the UI, the change should reflect in both web UIs
and the mobile UIs when necessary." Nothing enforced that. The app's screens
in mobile/src/app/ are the portal's pages in the portal's words, carried by
the API; tests pin a few of the shared sentences after the fact; and a
session editing one surface had only its memory to tell it about the other.
This hook makes the reminder the harness's job. .claude/settings.json runs it
after every Edit, Write, MultiEdit, NotebookEdit and Bash call. It reads the
hook's JSON from stdin once and, when the call wrote to a path in SURFACES,
prints one JSON object whose additionalContext names the surface, the file,
its counterparts and the instruction to invoke the `ui-parity` skill before
finishing. Otherwise it prints nothing at all.

What it holds itself to, because it runs on every tool call:

- stdlib only, no network, no file writes, one read of stdin;
- it never raises and always exits 0. A failing hook turns the tool call it
  follows into an error, and a missed reminder is the cheaper failure;
- a Bash command counts as a write only in the shapes a session uses to edit
  a file: `sed -i`, a `>`/`>>` redirection (`cat > file`), `tee`, `mv`/`cp`
  TO the path, a `python ... <<` heredoc. A `grep`, `cat`, `sed -n`, or a
  `cp` FROM a UI file merely reads it, and prints nothing;
- paths match by suffix after $CLAUDE_PROJECT_DIR, the hook's `cwd` and the
  process's cwd are stripped, so an absolute path under another checkout of
  this repository still matches.

tests/test_ui_parity_hook.py drives it as a subprocess and holds
.claude/skills/ui-parity/SKILL.md to naming every path SURFACES knows, so the
map in the skill and the map in the hook cannot drift apart.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

SKILL = ".claude/skills/ui-parity/SKILL.md"

# (an exact relative path, or a directory prefix ending in "/"; the surface;
# its counterparts). The first row that matches wins, so a specific row stands
# before the directory that holds it. The skill's surface map is the prose
# version of this list; the test holds the two together.
SURFACES: list[tuple[str, str, str]] = [
    ("sentinel/portal.py",
     "the member portal (web): its pages, _PORTAL_STYLE and the copy constants",
     "the app screen that mirrors the page under mobile/src/app/ (landing.tsx, sign-in.tsx, (tabs)/index.tsx, "
     "(tabs)/alerts.tsx, alert/[id].tsx, (tabs)/settings.tsx, activity.tsx, limits.tsx); mobile/src/theme/tokens.json "
     "for the :root tokens; sentinel/api.py and api/openapi.yaml for the route that carries the copy to the app; "
     "design/portal/ and docs/portal-design.md for the record"),
    ("sentinel/dashboard.py",
     "the ops dashboard (web, /admin)",
     "no app counterpart - the dashboard is web-only; design/dashboard/DESIGN-SYSTEM.md is its record, and "
     "sentinel/brand.py is all it may share with the portal (tests/test_surface_styles.py)"),
    ("sentinel/brand.py",
     "the Minas identity constants, drawn by the portal and the dashboard",
     "mobile/src/theme/tokens.json (the app's copy of the identity hues, held equal by tests/test_mobile_app.py); "
     "design/brand/ (the logo master); sentinel/static/ and mobile/assets/images/ (the derived logo files)"),
    ("sentinel/static/",
     "the portal's served brand files, derived from design/brand/ and never hand-edited",
     "design/brand/ is the master: rerun scripts/brand-assets.py derive, which also writes mobile/assets/images/ "
     "for the app; mobile/app.json names those files"),
    ("sentinel/api.py",
     "the app API: what the app can show, in the portal's words",
     "api/openapi.yaml (the contract tests/test_api_contract.py holds the server to); mobile/src/api/schema.d.ts "
     "(regenerate with npm run gen:api in mobile/); the app screen under mobile/src/app/ that reads the route; "
     "the portal page in sentinel/portal.py the route mirrors"),
    ("api/openapi.yaml",
     "the API contract between the server and the app",
     "sentinel/api.py (held to it by tests/test_api_contract.py); mobile/src/api/schema.d.ts (npm run gen:api, "
     "committed - CI fails on a difference); the app screens that read the changed route"),
    ("sentinel/alerts/base.py",
     "the customer-facing advice copy (advice_steps, advice_lead, advice_note, runbook)",
     "the alert email and SMS; the portal's incident page in sentinel/portal.py; the advice object of "
     "GET /api/v1/alerts/{id} in sentinel/api.py and api/openapi.yaml; the app's incident screen "
     "mobile/src/app/alert/[id].tsx"),
    ("design/portal/",
     "the portal's design reference: the mockups, styles.css and DESIGN-SYSTEM.md",
     "sentinel/portal.py _PORTAL_STYLE (the port of styles.css) and docs/portal-design.md; the app's primitives in "
     "mobile/src/ui/ and tokens in mobile/src/theme/ follow the same system"),
    ("design/dashboard/",
     "the dashboard's design reference (DESIGN-SYSTEM.md)",
     "sentinel/dashboard.py _STYLE; no app counterpart"),
    ("design/brand/",
     "the logo master and the store artwork",
     "scripts/brand-assets.py derive writes sentinel/static/ (portal and dashboard) and mobile/assets/images/ (the "
     "app, named by mobile/app.json); tests/test_mobile_app.py and tests/test_brand_assets.py pin the copies"),
    ("design/",
     "the design reference",
     "the surface the file describes: sentinel/portal.py, sentinel/dashboard.py, or the brand files under "
     "sentinel/static/ and mobile/assets/images/"),
    ("mobile/src/app/",
     "the app's screens",
     "the portal page each screen mirrors in sentinel/portal.py - landing.tsx <-> _render_landing, sign-in.tsx <-> "
     "_render_login, (tabs)/index.tsx <-> _render_home and _render_status, (tabs)/alerts.tsx <-> "
     "_render_alert_history, alert/[id].tsx <-> _render_incident, (tabs)/settings.tsx <-> _render_security, "
     "activity.tsx <-> _render_activity, limits.tsx <-> _render_limits - and the API route between them in "
     "sentinel/api.py and api/openapi.yaml; tests/test_mobile_app.py pins the sentences both must say"),
    ("mobile/src/theme/",
     "the app's tokens and theme",
     "sentinel/portal.py _PORTAL_STYLE :root (tests/test_mobile_app.py holds tokens.json equal to it) and "
     "sentinel/brand.py"),
    ("mobile/src/ui/",
     "the app's primitives (Screen, Title, Eyebrow, Body, Card, Section, Badge, KV, Button, Notice)",
     "the portal's primitives in sentinel/portal.py _PORTAL_STYLE (.panel, .badge, .notice, .btn, .kv, .steps, "
     ".feature-list, .phish) and design/portal/DESIGN-SYSTEM.md, their contract"),
    ("mobile/src/auth/",
     "the app's sign-in mechanics (decision 21c, fixed)",
     "the portal's sign-in flow in sentinel/portal.py (_render_login, PRE_AUTH) and the session routes in "
     "sentinel/api.py; a change here is behaviour, not copy - find the surface that states the rule the member reads"),
    ("mobile/src/api/",
     "the app's API client and generated schema",
     "api/openapi.yaml and sentinel/api.py; schema.d.ts is generated by npm run gen:api, never edited by hand"),
    ("mobile/src/",
     "the app",
     "the web page the change mirrors in sentinel/portal.py, and sentinel/api.py with api/openapi.yaml when the "
     "API carries it"),
    ("mobile/app.json",
     "the app's store identity and launch (name, icon, splash, colours)",
     "design/brand/ through scripts/brand-assets.py, and sentinel/portal.py _PORTAL_STYLE --bg, the ground the "
     "splash and the icons sit on; tests/test_mobile_app.py pins all of it"),
    ("mobile/assets/",
     "the app's derived brand files, never hand-edited",
     "design/brand/ is the master: rerun scripts/brand-assets.py derive, which also writes sentinel/static/ for "
     "the portal"),
]

# The tools whose tool_input names the file they changed.
EDITORS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

# Bash: how a path is found in a command, and which words wrap a command
# without being it (`sudo sed -i ...`, `xargs sed -i ...`).
_PATHISH = re.compile(r"[\w./@()\[\]~+-]+")
_CLEAN_PATH = re.compile(r"^[\w./@()\[\]~+-]+$")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = frozenset({"sudo", "xargs", "env", "nice", "time", "command", "exec", "nohup", "timeout"})
# A redirection's target: `> f`, `>> f`, `2> f`, `&> f`, `>| f`. `2>&1` and
# `>&2` have no target and do not match; /dev/null is dropped afterwards.
_REDIRECT = re.compile(r"(?<![<>=-])(?:\d|&)?>{1,2}\|?\s*([^\s;&|<>]+)")
_SEGMENTS = re.compile(r"\|\||&&|[;|\n]")
_STRIP = "\"'`,;:()<>"


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:  # an unbalanced quote, e.g. inside a heredoc body
        return segment.split()


def _base(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _command_word(tokens: list[str]) -> int | None:
    """The index of the token that is the command: past `VAR=x` assignments,
    wrappers and their flags (`timeout 10`, `nice -n 5`)."""
    for index, token in enumerate(tokens):
        if _ASSIGNMENT.match(token) or _base(token) in _WRAPPERS or token.startswith("-") or token.isdigit():
            continue
        return index
    return None


def _sed_in_place(token: str) -> bool:
    if token.startswith("--"):
        return token == "--in-place" or token.startswith("--in-place=")
    # -i, -i.bak, -Ei, -ni: of sed's single-letter options only `i` writes.
    return token.startswith("-") and len(token) > 1 and "i" in token[1:]


def bash_written_paths(command: str) -> list[str]:
    """The paths a Bash command writes, in the shapes this hook recognises.
    Empty when the command only reads - then nothing it mentions counts."""
    written: list[str] = []
    for segment in _SEGMENTS.split(command):
        for target in _REDIRECT.findall(segment):
            if target != "/dev/null":
                written.append(target)
        tokens = _tokens(segment)
        index = _command_word(tokens)
        if index is None:
            continue
        name = _base(tokens[index])
        rest = tokens[index + 1:]
        args = [token for token in rest if not token.startswith("-")]
        edits_in_place = name == "sed" and any(_sed_in_place(token) for token in rest)
        if edits_in_place or name == "tee":
            written.extend(args)
        elif name in ("mv", "cp") and len(args) >= 2:
            written.append(args[-1])
        elif name.startswith("python") and "<<" in segment:
            # A heredoc script can write any path it names; the body is in the command.
            written.extend(_PATHISH.findall(command))
    return written


def _strings_in(value, budget: int = 200_000) -> list[str]:
    """Every clean path-like token in the string values of a JSON value - the
    harness may list changed files in tool_response. Bounded, so a huge
    stdout cannot make the hook slow."""
    found: list[str] = []
    stack = [value]
    while stack and budget > 0:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            text = item[:budget]
            budget -= len(text)
            found.extend(token for token in re.split(r"[\s\"'`]+", text) if _CLEAN_PATH.match(token))
    return found


def relative(token: str, roots: list[str]) -> str:
    """The path with its quotes and any known root stripped."""
    path = token.strip().strip(_STRIP)
    if path.startswith("./"):
        path = path[2:]
    for root in roots:
        base = root.rstrip("/")
        if base and path.startswith(base + "/"):
            path = path[len(base) + 1:]
    return path


def match(path: str) -> tuple[str, str, str] | None:
    """The SURFACES row a path belongs to. A relative path (the known roots
    already stripped) must start with the row; an absolute path under a root
    this hook does not know matches by suffix, so another checkout of the
    repository still counts. docs/design/x.md is not design/."""
    absolute = path.startswith(("/", "~"))
    for known, surface, counterparts in SURFACES:
        if known.endswith("/"):
            hit = path.startswith(known) or (absolute and "/" + known in path)
        else:
            hit = path == known or (absolute and path.endswith("/" + known))
        if hit:
            return known, surface, counterparts
    return None


def match_paths(candidates: list[str], roots: list[str]) -> list[tuple[str, str, str, str]]:
    found: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = relative(candidate, roots)
        hit = match(path)
        if hit is not None and hit[0] not in seen:
            seen.add(hit[0])
            found.append((path, *hit))
    return found


def paragraph(matches: list[tuple[str, str, str, str]]) -> str:
    changed = " ".join(f"{path} is {surface}; its counterparts: {counterparts}."
                       for path, _known, surface, counterparts in matches)
    return (
        "UI surface changed. " + changed +
        " The owner's rule (decision 25, 2026-10-24): a change to what a member sees, reads or can do lands on "
        "the web and in the app whenever the surface has a counterpart. Before finishing, invoke the `ui-parity` "
        f"skill ({SKILL}): walk its surface map, carry the change to every counterpart or say in the commit message "
        "why this surface has none, and run the parity guards it names."
    )


def reminder(raw: str, *, project_dir: str | None = None, cwd: str | None = None) -> str:
    """The additionalContext for one hook payload, or "" when no surface was written."""
    payload = json.loads(raw) if raw.strip() else None
    if not isinstance(payload, dict):
        return ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    roots = [root for root in (project_dir, payload.get("cwd"), cwd) if isinstance(root, str) and root]
    tool = payload.get("tool_name")
    candidates: list[str] = []
    if tool in EDITORS:
        candidates.extend(value for key in ("file_path", "notebook_path")
                          if isinstance(value := tool_input.get(key), str))
    elif tool == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            written = bash_written_paths(command)
            if written:
                candidates.extend(written)
                candidates.extend(_strings_in(payload.get("tool_response")))
    matches = match_paths(candidates, roots)
    return paragraph(matches) if matches else ""


def main() -> int:
    try:
        context = reminder(sys.stdin.read(), project_dir=os.environ.get("CLAUDE_PROJECT_DIR"), cwd=os.getcwd())
        if context:
            output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}
            sys.stdout.write(json.dumps(output) + "\n")
            sys.stdout.flush()
    except Exception:  # a hook must never fail the tool call it follows
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
