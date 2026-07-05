from __future__ import annotations

import logging
from typing import Any

import ccxt

from sentinel.crypto import redact_secrets

logger = logging.getLogger("sentinel.exchange")

# Confirmed against live CCXT source (python/ccxt/bitget.py, master, fetched
# 2026-07-02): 'v2/spot/account/info' exists under private.spot.get (weight 20).
# It returns the CALLING key's own authorities/ips fields - Bitget exposes no
# endpoint to list other keys on the account (see docs/phase0-capability-audit.md
# section 1, "New API key created" row). CCXT Python implicit methods are
# exposed in snake_case per CCXT's documented cross-exchange naming convention.
ACCOUNT_INFO_METHOD = "private_spot_get_v2_spot_account_info"

# Confirmed against live CCXT source (python/ccxt/bybit.py, master, fetched
# 2026-07-05, line 431): 'v5/user/query-api' -> implicit method
# private_get_v5_user_query_api. Returns the CALLING key's own readOnly flag
# (0=read+write, 1=read-only) and permissions object. This is the authoritative
# read-only signal for the onboarding gate (see onboarding.py).
BYBIT_QUERY_API_METHOD = "private_get_v5_user_query_api"

SUPPORTED_EXCHANGES = ("bitget", "bybit")


class TamperSignal(Exception):
    """Raised when the exchange rejects our credentials with an auth/permission
    error - our monitoring key may have been deleted or had its permissions
    changed. This is a first-class detection, not just an ops error."""


class ExchangeClient:
    """Base for the read-only exchange wrappers. Holds credential redaction and
    the auth-error -> TamperSignal mapping shared by every exchange. Subclasses
    only ever expose read methods - Sentinel never calls a trade, withdraw, or
    transfer-out endpoint on a customer key."""

    exchange_id: str = ""

    def __init__(self, exchange, api_key: str, secret: str, password: str = ""):
        self._exchange = exchange
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

    # --- read surface every detector consumes; subclasses override as needed --
    def fetch_balance(self) -> dict:
        return self._call(self._exchange.fetch_balance)

    def fetch_withdrawals(self, since: int | None = None) -> list[dict]:
        return self._call(self._exchange.fetch_withdrawals, code=None, since=since)

    def fetch_deposits(self, since: int | None = None) -> list[dict]:
        return self._call(self._exchange.fetch_deposits, code=None, since=since)

    def fetch_closed_orders(self, since: int | None = None) -> list[dict]:
        return self._call(self._exchange.fetch_closed_orders, symbol=None, since=since)

    def fetch_transfers(self, code: str, since: int | None = None) -> list[dict]:
        return self._call(self._exchange.fetch_transfers, code=code, since=since)

    def fetch_tickers(self, symbols: list[str]) -> dict:
        """Public reference prices for off-market fill detection."""
        return self._call(self._exchange.fetch_tickers, symbols=symbols)

    def fetch_account_info(self) -> dict:
        raise NotImplementedError


class BitgetClient(ExchangeClient):
    """Thin, read-only wrapper around ccxt.bitget. Never calls a trade,
    withdraw, or transfer endpoint - Sentinel only ever reads."""

    exchange_id = "bitget"

    def __init__(self, api_key: str, secret: str, password: str):
        super().__init__(
            ccxt.bitget(
                {"apiKey": api_key, "secret": secret, "password": password, "enableRateLimit": True}
            ),
            api_key,
            secret,
            password,
        )

    def fetch_my_trades(self, symbol: str, since: int | None = None) -> list[dict]:
        # CCXT's bitget.fetchMyTrades() raises ArgumentsRequired without a
        # symbol for classic (non-UTA) accounts - confirmed against the
        # installed ccxt source. Not usable for an all-symbols reconciliation
        # pull; see fetch_closed_orders() below for that instead.
        return self._call(self._exchange.fetch_my_trades, symbol=symbol, since=since)

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


class BybitClient(ExchangeClient):
    """Thin, read-only wrapper around ccxt.bybit. Bybit v5 has no API
    passphrase. Read-only status is authoritative via the query-api endpoint
    (readOnly flag). See docs/phase0-capability-audit.md (Bybit addendum)."""

    exchange_id = "bybit"
    # Cross-product order coverage: a naive fetch_closed_orders defaults to
    # category=linear (USDT perps) only. Confirmed against CCXT bybit source.
    _ORDER_CATEGORIES = (
        {"type": "spot"},
        {"subType": "linear"},
        {"subType": "inverse"},
        {"type": "option"},
    )
    # Bybit fetch_balance defaults to the CONTRACT wallet; try the widest wallet
    # first. UNIFIED covers a UTA account's whole balance in one call.
    _BALANCE_TYPES = ("unified", "spot", "swap")

    def __init__(self, api_key: str, secret: str, password: str = ""):
        super().__init__(
            ccxt.bybit({"apiKey": api_key, "secret": secret, "enableRateLimit": True}),
            api_key,
            secret,
            "",  # no passphrase on Bybit; nothing to redact
        )

    def fetch_balance(self) -> dict:
        last_exc: Exception | None = None
        for wallet in self._BALANCE_TYPES:
            try:
                return self._call(self._exchange.fetch_balance, params={"type": wallet})
            except TamperSignal:
                raise  # auth failure is a tamper signal, not a wallet-type miss
            except Exception as exc:  # noqa: BLE001 - try the next wallet type
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        return {}

    def fetch_closed_orders(self, since: int | None = None) -> list[dict]:
        orders: list[dict] = []
        for params in self._ORDER_CATEGORIES:
            try:
                orders.extend(
                    self._call(self._exchange.fetch_closed_orders, symbol=None, since=since, params=dict(params))
                )
            except TamperSignal:
                raise
            except Exception as exc:  # noqa: BLE001 - a disabled product category must not kill the pull
                logger.debug("bybit closed-orders %s unavailable: %s", params, self._redact(str(exc)))
        return orders

    def fetch_account_info(self) -> dict:
        """Bybit query-api: the calling key's own readOnly flag, permissions,
        and IP allow-list. Normalized so the shared poller/onboarding code can
        read `data.readOnly`, `data.permissions`, `data.authorities` (aliased to
        permissions for the drift detector), and `data.ips`."""
        method = getattr(self._exchange, BYBIT_QUERY_API_METHOD, None)
        if method is None:
            raise RuntimeError(
                f"ccxt.bybit has no implicit method '{BYBIT_QUERY_API_METHOD}' - "
                "CCXT's Bybit endpoint map may have changed since this was verified "
                "against source on 2026-07-05."
            )
        raw = self._call(method)
        result = raw.get("result", raw) if isinstance(raw, dict) else {}
        data = dict(result) if isinstance(result, dict) else {}
        data.setdefault("authorities", data.get("permissions"))
        return {"data": data}


def make_client(exchange: str, api_key: str, secret: str, passphrase: str = "") -> ExchangeClient:
    """Factory: builds the read-only client for a given exchange id."""
    if exchange == "bitget":
        return BitgetClient(api_key, secret, passphrase)
    if exchange == "bybit":
        return BybitClient(api_key, secret)
    raise ValueError(f"unsupported exchange '{exchange}' (supported: {', '.join(SUPPORTED_EXCHANGES)})")
