# Release notes

The contract for the body of every GitHub Release. **The reference is the
owner's hand-written v1.7.0 body** (2026-08-22) — this file captures its
structure so every Release, cut or backfilled, reads like that one. The
CHANGELOG stays the engineering record; the Release body is the same facts
recomposed for a reader who operates or uses the product and may not read
code. A rewrite into this contract, never a copy-paste of the raw section.

`scripts/github-release.sh` composes a body to this contract from the
version's CHANGELOG section at cut time. What a machine cannot write well —
the opening line, most of "What you need to do" — it derives conservatively
from what the changelog actually records; `gh release edit <tag>` afterwards
is presentation and touches no tag.

## The sections, in order, and why each exists

**Opening line or two — no heading.** What this release is, in plain
language, and whether the monitor's behaviour moved ("No change to what the
monitor detects or who it alerts."). A reader who stops here still knows
whether they care.

**`## What you need to do`** — actions required of an operator or a member:
sign-outs, migrations, config changes. Lead bold, imperative, concrete
("Everyone signs in to the portal once after this upgrade."), then why, then
what is NOT required ("Nothing else is required — no data migration, no
configuration change."). **Omit the section entirely when there are none; do
not write "nothing"** — an empty section teaches readers to skip it, and the
day it matters they will.

**The authoring rule that feeds this section: an upgrade effect MUST carry
the words "on upgrade" in its CHANGELOG entry.** That exact phrase is what
`github-release.sh` lifts into "What you need to do" at cut time — an
upgrade effect phrased any other way ("after deploying", "existing sessions
end", "operators should then…") is silently absent from the Release, which
is the worst place for an operator action to go missing. The convention is
stated here, in the contract, precisely so it is not discoverable only by
reading the script. v1.7.0's #31 entry is the model: "One-time effect **on
upgrade**: every existing portal session is signed out…".

**`## Changed detection behaviour`** — always present, always explicit, even
when the answer is **None**. This is a security monitor; a reader must never
have to infer whether detection moved. When it is None, say what the release
touches instead, so "None" reads as a checked answer rather than a template
left blank. Where a version's changelog did not record the answer (the
sections predating the convention), the Release says so plainly — "not
recorded" is the truth, and guessing "None" would be inventing a safety
claim.

**The change sections — `## Fixed`, `## Added`, `## Changed`.** Each entry:

- a **bolded plain-language sentence a non-engineer understands**, with the
  issue reference — the sentence states the effect on a person ("A stranger
  who knows your email can no longer lock you out"), not the mechanism;
- then the detail: what was wrong, what changed, in prose;
- then **the honest residue where one exists**, named as such ("The honest
  residue: a distributed guesser is throttled per source only…") — what the
  fix deliberately does not cover, and why that trade was made. Residue
  omitted is residue discovered by a customer.

**`## Deploy`** — the exact command with the version in it, as an indented
code line:

    sudo /opt/minas-guardian/scripts/deploy.sh vX.Y.Z

Never "deploy as usual"; the command is the contract, and deploy-gate.sh
holds the other end of it.

**`**Full changelog:**`** — the compare link against the previous tag, taken
from the link CHANGELOG.md already maintains, so the Release and the
changelog cannot disagree about what "since last time" means.

## Rules that hold everywhere

- The Release is presentation for a tag, never the other way round: nothing
  in this pipeline creates or moves a tag (`--verify-tag` on every create).
- A version that never reached production says so at the top of its body
  (v1.5.0 is the standing example: tagged, CI-green, never deployed).
- Backfilled bodies follow the same contract as new cuts; a reader must not
  be able to date a Release by its format.
