"""The test environment is scrubbed to an allowlist, and that is pinned here.

A suite that can see a real credential is a suite that can spend money, mail a
customer, or authenticate against a live exchange - by accident, from a test
that never meant to. The scrubbing lives in conftest; this file exists so that
removing or narrowing it fails rather than going quiet.

Both halves are tested, because they fail differently. The policy function is
tested against names that exist in nobody's environment today - that is the
whole point of an allowlist - and the applied fixture is tested by poisoning a
real environment and running a real test in it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import ENV_ALLOWLIST, env_is_allowed, env_names_to_clear

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- the policy -------------------------------------------------------------


@pytest.mark.parametrize("name", [
    # The gap that prompted this: the denylist named SMTP_ and would have said
    # nothing the day Resend moved from SMTP to its HTTP API.
    "RESEND_API_KEY",
    # Exchange credentials - the keys this product exists to protect.
    "BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE",
    "BINANCE_API_KEY", "COINBASE_API_SECRET",
    # The key that decrypts every customer credential at rest.
    "SENTINEL_MASTER_KEY",
    # The providers the old denylist did cover, which must stay covered.
    "SMTP_PASSWORD", "SMTP_HOST", "CLICKSEND_API_KEY", "TWILIO_AUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY",
    # CI tokens: GITHUB_* is deliberately not an allowed prefix.
    "GITHUB_TOKEN", "GH_TOKEN", "NPM_TOKEN", "AWS_SECRET_ACCESS_KEY",
    # The case a denylist can never have: an integration nobody has added yet.
    "SOME_FUTURE_PROVIDER_TOKEN", "PAYMENTS_WEBHOOK_SIGNING_SECRET",
])
def test_a_credential_shaped_variable_is_cleared(name):
    assert not env_is_allowed(name)
    assert name in env_names_to_clear([name])


@pytest.mark.parametrize("name", ["PATH", "HOME", "TMPDIR", "VIRTUAL_ENV", "LANG"])
def test_process_plumbing_survives(name):
    """Cleared too aggressively and nothing runs at all - subprocesses lose
    their interpreter, temp files lose their directory."""
    assert env_is_allowed(name)
    assert env_names_to_clear([name]) == []


@pytest.mark.parametrize("name", ["PYTEST_CURRENT_TEST", "LC_ALL", "XDG_CACHE_HOME",
                                  "COVERAGE_FILE", "COV_CORE_SOURCE"])
def test_allowed_prefixes_survive(name):
    assert env_is_allowed(name)


def test_the_mutmut_selector_survives():
    """Not plumbing for this suite - plumbing for the gate that measures it.

    `MUTANT_UNDER_TEST` is how mutmut tells its copy of the package which
    mutant is live. Clear it and every mutant runs as unmutated source: the
    tests all pass, every mutant is scored as surviving, and the mutation gate
    reports on a run in which nothing was ever mutated. This suite cannot
    notice that about itself, so the pin lives here.

    Found the hard way - the allowlist landed without it and mutmut refused to
    start, with `FAILED: Unable to force test failures`."""
    assert env_is_allowed("MUTANT_UNDER_TEST")
    assert env_names_to_clear(["MUTANT_UNDER_TEST"]) == []


def test_the_allowlist_stays_small():
    """A guard on the direction of travel. This list grows one variable at a
    time, each with a reason; if it has grown a lot, the policy has quietly
    become a denylist again."""
    assert len(ENV_ALLOWLIST) < 40


def test_github_token_is_not_rescued_by_a_prefix():
    """GITHUB_ is a tempting prefix to allow for CI metadata, and GITHUB_TOKEN
    is a live credential sitting inside it."""
    assert not env_is_allowed("GITHUB_TOKEN")
    assert not env_is_allowed("GITHUB_ACTIONS")


# --- the applied fixture ----------------------------------------------------


_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|API)", re.I)


def _looks_like_a_secret_value(value: str) -> bool:
    """Opaque, long, and not a path or a sentence."""
    if len(value) < 20 or any(c in value for c in " /:\\\t"):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_\-.+=]{20,}", value)) and any(c.isdigit() for c in value)


def test_no_credential_shaped_variable_is_visible():
    """Run inside a scrubbed environment, so this reads whatever survived."""
    offenders = []
    for name, value in os.environ.items():
        if env_is_allowed(name):
            continue
        if _SECRET_NAME.search(name) or _looks_like_a_secret_value(value):
            offenders.append(name)
    assert offenders == [], f"credential-shaped variables reached a test: {offenders}"


def test_the_scrub_holds_against_a_poisoned_parent_environment():
    """The end-to-end pin, and the only one that proves the fixture is wired.

    Everything above tests the policy; a policy nobody applies is the failure
    this repo keeps rediscovering. So: put live-looking credentials in a real
    parent environment, run a real test in a child process, and assert it
    cannot see them."""
    poisoned = {
        **os.environ,
        "RESEND_API_KEY": "re_" + "A1b2C3d4" * 4,
        "BITGET_API_SECRET": "9f" * 20,
        "SENTINEL_MASTER_KEY": "kQ7" + "x9" * 18,
        "SMTP_PASSWORD": "re_" + "Z9y8X7w6" * 4,
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         "tests/test_environment_isolation.py::test_no_credential_shaped_variable_is_visible"],
        cwd=REPO_ROOT, env=poisoned, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        "a poisoned parent environment reached the child test:\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-1000:]}"
    )
