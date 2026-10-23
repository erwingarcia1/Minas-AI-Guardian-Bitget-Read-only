from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

from sentinel import outbox, twofactor
from sentinel.config import RulesConfig
from sentinel.crypto import CredentialCipher
from sentinel.db import Database

RULES_YAML = """
polling:
  fast_loop_seconds: "45"
  slow_loop_seconds: "600"
  jitter_seconds: "10"
detectors:
  quantity_drop:
    tolerance_pct: "0.5"
    tolerance_absolute: "0.00000001"
  tamper:
    consecutive_auth_failures_threshold: 2
  heartbeat:
    consecutive_failures_threshold: 5
alerting:
  cooldown_minutes:
    CRITICAL: "15"
    HIGH: "30"
    MEDIUM: "720"
    LOW: "1440"
  escalation_unacknowledged_minutes: "15"
"""


# Everything a test is allowed to see. Deliberately short: this is process
# plumbing, not configuration. Anything a test needs beyond it, it sets itself
# with monkeypatch - which is also how the test states what it depends on.
#
# An allowlist rather than a denylist, because a denylist protects only the
# providers that existed when it was written. The first version named SMTP_,
# CLICKSEND_, TWILIO_ and TELEGRAM_, and would have gone on passing while
# saying nothing about RESEND_API_KEY, about exchange keys, or about whatever
# the next integration is called. The failure mode of a denylist is silence.
ENV_ALLOWLIST = frozenset({
    # identity and filesystem
    "HOME", "PWD", "OLDPWD", "USER", "LOGNAME", "HOSTNAME", "SHELL",
    "TMPDIR", "TEMP", "TMP",
    # locating and running the interpreter
    "PATH", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHASHSEED",
    "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    "PYTHONUTF8", "PYTHONBREAKPOINT",
    # terminal and locale, which change output formatting rather than behaviour
    "TERM", "COLUMNS", "LINES", "LANG", "LANGUAGE", "TZ",
    # CI plumbing. Note GITHUB_* is NOT allowed: GITHUB_TOKEN lives there.
    "CI", "SOURCE_DATE_EPOCH",
    # How mutmut tells the mutated package which mutant is live. Strip it and
    # every mutant runs as unmutated source, so every test passes and every
    # mutant "survives" - mutmut catches this up front ("Unable to force test
    # failures") and refuses to run, which is how it was found. Not a secret,
    # and only load-bearing inside mutmut's own copy of the package.
    "MUTANT_UNDER_TEST",
})

ENV_ALLOWED_PREFIXES = ("PYTEST_", "LC_", "XDG_", "COVERAGE_", "COV_CORE_")


def env_is_allowed(name: str) -> bool:
    return name in ENV_ALLOWLIST or name.startswith(ENV_ALLOWED_PREFIXES)


def env_names_to_clear(names) -> list[str]:
    """The policy, as a function, so it can be tested against names that are
    not in anybody's environment today - which is the case that matters."""
    return sorted(n for n in names if not env_is_allowed(n))


@pytest.fixture(autouse=True)
def only_allowlisted_environment(monkeypatch):
    """No test may see a real credential, whatever is in the shell.

    `sentinel.cli.main` calls `load_dotenv()`, which writes the operator's real
    .env into `os.environ` for the remainder of the process. One test touching
    the CLI entrypoint therefore hands live credentials to every test that runs
    after it, in file order - and those tests then use them. The symptom was a
    reset-timing test failing on a 3s SMTP round trip instead of on the timing
    property it exists to measure, which is the benign version; the malign
    version is a suite that bills the account, mails real addresses, or
    authenticates against a real exchange with a real key.

    Cleared before the test body, so a test that wants a transport still
    monkeypatches a fake one in and wins."""
    for name in env_names_to_clear(os.environ):
        monkeypatch.delenv(name, raising=False)
    # Set after the clearing, not allowlisted through it: an operator shell
    # with SENTINEL_ENV=production must not be able to hand that value to the
    # suite. `sentinel.net` reads it and refuses to open a socket.
    monkeypatch.setenv("SENTINEL_ENV", "test")


# The instant every TOTP code in this suite is built for and judged against
# (#72). Arbitrary, and fixed: 2026-01-01T00:00:00Z.
PINNED_TOTP_NOW = 1_767_225_600.0


@pytest.fixture(autouse=True)
def pinned_totp_clock(monkeypatch):
    """One clock reading, not two, wherever a code is verified.

    `confirm_enrolment` and `check_second_factor` read the clock themselves. A
    test that builds a code from a *separate* reading is therefore asserting
    that the two land in the same 30-second step - true almost always, and
    silently wrong when it isn't. The call then returns False because the code
    expired rather than because the guard under test fired, and a test asserting
    `is False` cannot tell those apart.

    That is not hypothetical: the `#32 ATTACK sign in with an unconfirmed
    enrolment` probe survived a health-check run, meaning the 2FA guard could
    have been deleted with the suite green. It has never reproduced, and it does
    not need to - the dependency is visible in the source, and this removes it.

    Autouse and global rather than per-file, because the next TOTP test written
    would otherwise inherit the same shape. A test that genuinely wants clock
    drift can monkeypatch `_clock` itself and win, the same way the environment
    allowlist above lets a test put its own transport back."""
    monkeypatch.setattr(twofactor, "_clock", lambda: PINNED_TOTP_NOW)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    # A request answers first and delivers after, on its own thread
    # (sentinel/outbox.py): nothing a test's requests queued may outlive the
    # database it was queued against, or log its failure into the next test.
    # Bounded, like every wait in a test.
    try:
        assert outbox.wait_until_sent(10), "a delivery queued by this test was still running 10 s after it ended"
    finally:
        database.close()


@pytest.fixture
def cipher():
    return CredentialCipher(Fernet.generate_key().decode())


@pytest.fixture
def rules(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(RULES_YAML)
    return RulesConfig(rules_path)


def make_account(db, label="acct-1"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts "
            "(label, topology, encrypted_api_key, encrypted_secret, encrypted_passphrase, onboarded_at, updated_at) "
            "VALUES (?, 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')",
            (label,),
        )
        return cur.lastrowid
