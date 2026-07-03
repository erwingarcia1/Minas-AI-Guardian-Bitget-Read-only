from __future__ import annotations

from typing import Any

import ccxt

from sentinel.crypto import redact_secrets

# Confirmed against live CCXT source (python/ccxt/bitget.py, master, fetched
# 2026-07-02): 'v2/spot/account/info' exists under private.spot.get (weight 20).
# It returns the CALLING key's own authorities/ips fields - Bitget exposes no
# endpoint to list other keys on the account (see docs/phase0-capability-audit.md
# section 1, "New API key created" row). CCXT Python implicit methods are
# exposed in snake_case per CCXT's documented cross-exchange naming convention.
ACCOUNT_INFO_METHOD = "private_spot_get_v2_spot_account_info"


class TamperSignal(Exception):
    """Raised when Bitget rejects our credentials with an auth/permission
    error - our monitoring key may have been deleted or had its permissions
    changed. This is a first-class detection, not just an ops error."""


class BitgetClient:
    """Thin, read-only wrapper around ccxt.bitget. Never calls a trade,
    withdraw, or transfer endpoint - Sentinel only ever reads."""

    def __init__(self, api_key: str, secret: str, password: str):
        self._exchange = ccxt.bitget(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": password,
                "enableRateLimit": True,
            }
        )
        self._api_key = api_key
        self._secret = secret
        self._password = password

    def _redact(self, text: str) -> str:
        return redact_secrets(text, self._api_key, self._secret, self._password)

    def _call(self, fn, *args, **kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ccxt.AuthenticationError, ccxt.PermissionDenied) as exc:
            raise TamperSignal(self._redact(str(exc))) from exc
        except ccxt.BaseError as exc:
            # Scrub secrets before this propagates anywhere it might be logged.
            raise type(exc)(self._redact(str(exc))) from exc

    def fetch_balance(self) -> dict:
        return self._call(self._exchange.fetch_balance)

    def fetch_withdrawals(self, since: int | None = None) -> list[dict]:
        return self._call(self._exchange.fetch_withdrawals, code=None, since=since)

    def fetch_deposits(self, since: int | None = None) -> list[dict]:
        return self._call(self._exchange.fetch_deposits, code=None, since=since)

    def fetch_my_trades(self, symbol: str, since: int | None = None) -> list[dict]:
        # CCXT's bitget.fetchMyTrades() raises ArgumentsRequired without a
        # symbol for classic (non-UTA) accounts - confirmed against the
        # installed ccxt source. Not usable for an all-symbols reconciliation
        # pull; see fetch_closed_orders() below for that instead.
        return self._call(self._exchange.fetch_my_trades, symbol=symbol, since=since)

    def fetch_closed_orders(self, since: int | None = None) -> list[dict]:
        """Unlike fetch_my_trades, this does not require a symbol - confirmed
        against the installed ccxt source. Used as the fill/reconciliation
        data source for the quantity-drop detector so it can cover every
        traded pair in one call instead of needing a per-symbol loop."""
        return self._call(self._exchange.fetch_closed_orders, symbol=None, since=since)

    def fetch_transfers(self, code: str, since: int | None = None) -> list[dict]:
        # Bitget's transferRecords endpoint is per-coin; CCXT raises
        # ArgumentsRequired without a code (confirmed against installed source).
        # The poller loops over the account's top balance coins, capped by
        # detectors.transfers.max_coins_per_cycle.
        return self._call(self._exchange.fetch_transfers, code=code, since=since)

    def fetch_tickers(self, symbols: list[str]) -> dict:
        """Public reference prices for off-market fill detection."""
        return self._call(self._exchange.fetch_tickers, symbols=symbols)

    def fetch_account_info(self) -> dict:
        """Native (implicit) call - CCXT has no unified method for this.
        Used for the onboarding read-only permission check and for the
        best-effort 'new API key' proxy detector (see detectors/new_api_key.py)."""
        method = getattr(self._exchange, ACCOUNT_INFO_METHOD, None)
        if method is None:
            raise RuntimeError(
                f"ccxt.bitget has no implicit method '{ACCOUNT_INFO_METHOD}' - "
                "CCXT's Bitget endpoint map may have changed since this was verified "
                "against source on 2026-07-02. Re-check docs/phase0-capability-audit.md."
            )
        return self._call(method)
