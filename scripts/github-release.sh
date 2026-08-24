#!/usr/bin/env bash
# Publish the GitHub Release object for a tag that already exists (#94).
#
#   scripts/github-release.sh v1.2.3            # backfill / repair
#   scripts/github-release.sh v1.2.3 --latest   # what release.sh passes for a new cut
#
# The body is COMPOSED to the contract in docs/RELEASE-NOTES.md from that
# version's CHANGELOG section - the owner's hand-written v1.7.0 body is the
# template - not pasted raw: opening line, "What you need to do" (lifted
# from sentences the changelog marks with "on upgrade"; omitted when there
# are none), "Changed detection behaviour" always present (saying "not
# recorded" plainly when the section predates the convention, never
# guessing None), the change sections with their bold plain-language leads,
# the exact deploy command, and the compare link the changelog already
# maintains. Title = the tag.
#
# The Release is presentation for a tag, never the other way round: this
# script NEVER creates or moves a tag (`--verify-tag` makes gh refuse if the
# tag is not already on the remote).
#
# Idempotent on purpose: a Release that already exists is "nothing to do",
# exit 0 - so re-running a cut, or the backfill, cannot fail on its own
# earlier success. Everything unprovable is exit 1: gh missing or
# unauthenticated, no CHANGELOG section for the version, tag absent.
# Without --latest the Release is created with --latest=false, so
# backfilling an old tag can never steal the "Latest" badge from the
# newest release.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

die() { printf 'github-release: %s\n' "$1" >&2; exit 1; }

[ $# -ge 1 ] || die "usage: scripts/github-release.sh vX.Y.Z [--latest]"
TAG="$1"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "tag must be vX.Y.Z, got: $TAG"
VERSION="${TAG#v}"

LATEST="--latest=false"
[ "${2:-}" = "--latest" ] && LATEST="--latest"

command -v gh >/dev/null 2>&1 || die "gh is not installed - cannot publish a Release"
gh auth status >/dev/null 2>&1 || die "gh is not authenticated - cannot publish a Release"

if gh release view "$TAG" --json tagName >/dev/null 2>&1; then
  echo "github-release: $TAG already has a Release - nothing to do"
  exit 0
fi

NOTES=$(mktemp)
trap 'rm -f "$NOTES"' EXIT

python3 - "$VERSION" > "$NOTES" <<'EOF'
import re
import sys

version = sys.argv[1]
text = open("CHANGELOG.md").read()
lines = text.splitlines()
try:
    start = next(i for i, line in enumerate(lines)
                 if re.match(rf"## \[{re.escape(version)}\]", line))
except StopIteration:
    sys.stderr.write(f"CHANGELOG.md has no '## [{version}]' section - "
                     "a Release with no notes is the thing this repo refuses\n")
    raise SystemExit(1)
end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
           len(lines))
section = lines[start + 1:end]

link = re.search(rf"^\[{re.escape(version)}\]: (\S+)$", text, re.M)
if not link:
    sys.stderr.write(f"CHANGELOG.md has no compare link for [{version}] - "
                     "the Release and the changelog must agree on 'since last time'\n")
    raise SystemExit(1)
compare_url = link.group(1)


def trimmed(block):
    while block and not block[0].strip():
        block.pop(0)
    while block and not block[-1].strip():
        block.pop()
    return block


# Split the section into its ### subsections, keeping their order.
subs, title, cur = [], None, []
for line in section:
    if line.startswith("### "):
        if title is not None:
            subs.append((title, trimmed(cur)))
        title, cur = line[4:].strip(), []
    elif title is not None:
        cur.append(line)
if title is not None:
    subs.append((title, trimmed(cur)))


def entries(sub_lines):
    """Changelog bullets -> template entries: the bold plain-language lead
    (with its issue reference) as its own line, the detail as prose below.
    Non-bullet content passes through untouched."""
    bullets, stray, bullet = [], [], None
    for line in sub_lines:
        if line.startswith("- "):
            if bullet is not None:
                bullets.append(bullet)
            bullet = [line[2:]]
        elif bullet is not None and (line.startswith("  ") or not line.strip()):
            bullet.append(line[2:] if line.startswith("  ") else "")
        else:
            if bullet is not None:
                bullets.append(bullet)
                bullet = None
            stray.append(line)
    if bullet is not None:
        bullets.append(bullet)

    blocks = []
    joined_stray = "\n".join(trimmed(stray)).strip()
    if joined_stray:
        blocks.append(joined_stray)
    for b in bullets:
        body = "\n".join(trimmed(b))
        m = re.match(r"\*\*(.+?)\*\*\s*(\((#\d+[^)]*)\))?\.?\s*", body, re.S)
        if m:
            lead = " ".join(m.group(1).split()).rstrip(".")
            issue = f" ({m.group(3)})" if m.group(3) else ""
            rest = body[m.end():].strip()
            # Reflow the seam: consuming the lead from a hard-wrapped line
            # leaves that line's remainder orphaned ("The" alone), and GitHub
            # renders single newlines as breaks - so join it onto the next
            # line. Only the FIRST newline, and never across a blank line:
            # a paragraph break is content, the wrap point is not.
            first, sep, tail = rest.partition("\n")
            if sep and tail and not tail.startswith("\n"):
                rest = f"{first} {tail}"
            blocks.append(f"**{lead}**{issue}" + (f"\n\n{rest}" if rest else ""))
        else:
            blocks.append(body)
    return blocks


DETECTION = "Changed detection behaviour"
detection = next((c for t, c in subs if t == DETECTION), None)
changes = [(t, c) for t, c in subs if t != DETECTION]

# Opening: what this carries, and whether detection moved - a reader who
# stops after two lines still knows whether they care.
NUMBERS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
           6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}
KINDS = {"Fixed": ("fix", "fixes"), "Added": ("addition", "additions"),
         "Changed": ("change", "changes"), "Removed": ("removal", "removals"),
         "Security": ("security change", "security changes"),
         "Deprecated": ("deprecation", "deprecations")}
# Counts aggregate across same-titled subsections: the early mega-releases
# grouped their entries thematically, and "three additions, two additions,
# one addition..." is not an opening line.
agg = {}
for t, c in changes:
    agg[t] = agg.get(t, 0) + sum(1 for line in c if line.startswith("- "))
parts = [f"{NUMBERS.get(n, str(n)).lower()} {KINDS[t][0] if n == 1 else KINDS[t][1]}"
         for t, n in agg.items() if n and t in KINDS]
summary = (" and ".join([", ".join(parts[:-1]), parts[-1]])
           if len(parts) > 2 else " and ".join(parts)) if parts else "changes"
if detection is None:
    opening = (f"{(summary[:1].upper() + summary[1:])} - details below. Whether "
               "detection behaviour changed was not recorded for this version.")
elif any("**None" in line for line in detection):
    opening = (f"{(summary[:1].upper() + summary[1:])} - details below. No change "
               "to what the monitor detects or who it alerts.")
else:
    opening = (f"{(summary[:1].upper() + summary[1:])} - details below. Detection "
               "behaviour changes in this release; read that section first.")

out = [opening, ""]

# "What you need to do": sentences the changelog marks with "on upgrade".
# Omitted entirely when there are none - never written as "nothing".
flat = " ".join(" ".join(line for _, c in subs for line in c).split())
todo = [m.group(0).strip() for m in
        re.finditer(r"[^.!?]*\bon upgrade\b[^.!?]*[.!?]", flat, re.I)]
if todo:
    # Bold markers are stripped from lifted sentences: a lift that starts or
    # ends mid-bold leaves unbalanced ** that renders as literal asterisks,
    # and the section heading already carries the emphasis.
    out += ["## What you need to do", ""] + [" ".join(todo).replace("**", ""), ""]

out += ["## Changed detection behaviour", ""]
if detection is None:
    out += ["**Not recorded.** This version's changelog predates the rule that "
            "every release states whether detection moved; nothing should be "
            "inferred either way.", ""]
else:
    for block in entries(detection):
        out += [block, ""]

for t, c in changes:
    out += [f"## {t}", ""]
    for block in entries(c):
        out += [block, ""]

out += ["## Deploy", "",
        f"    sudo /opt/minas-guardian/scripts/deploy.sh v{version}", "",
        f"**Full changelog:** {compare_url}"]
print("\n".join(out))
EOF

gh release create "$TAG" --verify-tag --title "$TAG" --notes-file "$NOTES" "$LATEST" \
  || die "gh refused to create the Release for $TAG (is the tag pushed?)"

echo "github-release: created $TAG"
