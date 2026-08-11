from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from sentinel.rules_schema import validate

logger = logging.getLogger("sentinel.config")


class RulesConfigError(ValueError):
    """config/rules.yaml could not be trusted. Raised only when there is no
    previously-valid configuration to fall back on - i.e. at startup, where
    running with unknown thresholds would look exactly like monitoring."""


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
                self._reject(mtime, problems)
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

    def _reject(self, mtime: float | None, problems: list[str]) -> None:
        """Refuses the file on disk.

        Before startup has produced one valid load there is nothing to fall
        back to, so this raises: better to not come up than to come up with
        thresholds nobody has checked. Afterwards, detection continues on the
        last values known to be good - stopping detection because a threshold
        was mistyped is the failure this issue is about - and the rejection is
        reported loudly.

        `self._mtime` advances even on rejection, so one bad edit produces one
        ERROR rather than one per `get()`; every read calls `reload()`, and a
        single poll cycle reads a dozen thresholds per account. The operator's
        next edit changes the mtime again and is re-validated normally."""
        detail = "; ".join(problems)
        self._mtime = mtime
        self._validation_error = detail
        self._rejected_at = datetime.now(timezone.utc).isoformat()

        if not self._loaded:
            raise RulesConfigError(f"config/rules.yaml ({self._path}) rejected: {detail}")

        logger.error(
            "config/rules.yaml rejected: %s - KEEPING the last valid configuration; "
            "the values on disk are NOT in use until this is fixed",
            detail,
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
