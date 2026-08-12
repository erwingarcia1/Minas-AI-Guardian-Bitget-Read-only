from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from sentinel.rules_schema import prune_invalid, validate

logger = logging.getLogger("sentinel.config")


class RulesConfigError(ValueError):
    """A rules file could not be read or parsed at all.

    Raised by `sentinel validate-config`, which is where failing fast on a bad
    config belongs: before a deploy, not during one. `RulesConfig` itself never
    raises - see `_reject`."""


def _to_decimal_leaves(node: Any) -> Any:
    """Numeric-looking strings become Decimal so thresholds are never floats.
    Non-numeric strings (e.g. 'CRITICAL') pass through unchanged."""
    if isinstance(node, dict):
        return {k: _to_decimal_leaves(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_to_decimal_leaves(v) for v in node]
    if isinstance(node, str):
        try:
            return Decimal(node)
        except InvalidOperation:
            return node
    return node


class RulesConfig:
    """Hot-reloadable detection thresholds. Re-reads config/rules.yaml whenever
    its mtime changes, so tuning a threshold never requires a deploy.

    Every load is validated against `rules_schema.SPEC` before it is allowed to
    take effect (G-12). Prior to that, a bad value was found by whichever
    detector happened to read it first - which meant `Decimal("0,5")` raising
    `InvalidOperation` from the middle of `run_fast_loop`, *after* the poll had
    already recorded `last_success_at = now`, and being swallowed by the
    worker's blanket handler in `engine.py`. One mistyped character stopped
    every detector for every account while the dashboard showed healthy polls
    seconds old. A config that cannot be parsed must never reach a detector.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._data: dict[str, Any] = {}
        self._loaded = False
        self._validation_error: str | None = None
        self._rejected_at: str | None = None
        self.reload()

    # --- state an operator can see -----------------------------------------

    # These reload before answering, like the readers below. A dashboard that
    # asks "is the config healthy?" must not be told about a file state two
    # edits old - reporting stale health is the same failure mode as the one
    # this validation exists to close.

    @property
    def is_valid(self) -> bool:
        """False when the file on disk was rejected and the values in use are
        therefore older than the file. Surfaced on the dashboard and as a
        metric, because a log line alone is not a signal anyone receives."""
        self.reload()
        return self._validation_error is None

    @property
    def validation_error(self) -> str | None:
        self.reload()
        return self._validation_error

    @property
    def rejected_at(self) -> str | None:
        self.reload()
        return self._rejected_at

    # --- loading ------------------------------------------------------------

    def reload(self) -> None:
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime
            except OSError as exc:
                self._reject(None, [f"cannot be read: {exc}"])
                return
            if mtime == self._mtime:
                return

            try:
                with open(self._path, "r") as f:
                    raw = yaml.safe_load(f) or {}
            except yaml.YAMLError as exc:
                self._reject(mtime, [f"is not valid YAML: {exc}"])
                return

            converted = _to_decimal_leaves(raw)
            problems = validate(converted)
            if problems:
                self._reject(mtime, problems, parsed=converted)
                return

            self._data = converted
            self._mtime = mtime
            if self._validation_error is not None:
                logger.warning(
                    "config/rules.yaml is valid again - the values on disk are now in use (%s)",
                    self._path,
                )
            self._validation_error = None
            self._rejected_at = None
            self._loaded = True

    def _reject(self, mtime: float | None, problems: list[str], parsed=None) -> None:
        """Refuses the file on disk. Never raises, and never stops the process.

        This used to raise when no valid configuration had been loaded yet, on
        the reasoning that coming up with unchecked thresholds looks exactly
        like monitoring. That missed the case that matters most: an **upgrade**,
        where a file the old code ran happily on is rejected by a schema that
        shipped after it. Refusing there turns a working monitor into a dead one
        during a deploy - and dying loudly is still not monitoring.

        Two paths, and which one applies depends on whether there is anything
        better available:

        - **A valid configuration has been loaded before.** Keep it. It is both
          valid and what the operator actually asked for, which beats a pruned
          version of a broken file.
        - **Nothing valid has ever loaded** (startup on a bad file). Run on the
          settings that *do* validate, and let the readers fall through to their
          own defaults for the rest. Some detection beats none, in every case.

        What makes that defensible rather than merely quiet is the CRITICAL
        operator page raised from `Engine._check_config_health` - which only
        became possible when G-49 landed a paging route. The decision was
        revisited because its binding constraint went away, not because the risk
        did. Fail-fast now lives in `sentinel validate-config`, run before the
        deploy rather than during it.

        `self._mtime` advances even on rejection, so one bad edit produces one
        ERROR rather than one per `get()`; every read calls `reload()`, and a
        single poll cycle reads a dozen thresholds per account. The operator's
        next edit changes the mtime again and is re-validated normally."""
        detail = "; ".join(problems)
        self._mtime = mtime
        self._validation_error = detail
        self._rejected_at = datetime.now(timezone.utc).isoformat()

        if self._loaded:
            logger.error(
                "rules config rejected (%s): %s - KEEPING the last valid configuration; "
                "the values on disk are NOT in use until this is fixed",
                self._path, detail,
            )
            return

        self._data = prune_invalid(parsed) if parsed is not None else {}
        logger.error(
            "rules config rejected at startup (%s): %s - RUNNING ANYWAY on the settings that "
            "validate, with built-in defaults for the rest. Detection is active but is NOT "
            "using the rejected values. Fix the file; an operator page has been raised.",
            self._path, detail,
        )

    # --- readers ------------------------------------------------------------

    def get(self, *keys: str, default: Any = None) -> Any:
        self.reload()
        node: Any = self._data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def get_seconds(self, *keys: str, default: float = 0.0) -> float:
        value = self.get(*keys, default=default)
        return float(value)

    def get_int(self, *keys: str, default: int = 0) -> int:
        value = self.get(*keys, default=default)
        return int(value)

    def get_decimal(self, *keys: str, default: Decimal = Decimal("0")) -> Decimal:
        value = self.get(*keys, default=default)
        return value if isinstance(value, Decimal) else Decimal(str(value))
