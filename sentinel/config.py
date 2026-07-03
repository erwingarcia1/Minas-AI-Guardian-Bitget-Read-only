from __future__ import annotations

import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


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
    its mtime changes, so tuning a threshold never requires a deploy."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            mtime = self._path.stat().st_mtime
            if mtime == self._mtime:
                return
            with open(self._path, "r") as f:
                raw = yaml.safe_load(f) or {}
            self._data = _to_decimal_leaves(raw)
            self._mtime = mtime

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
