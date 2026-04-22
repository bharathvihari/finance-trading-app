from __future__ import annotations

import importlib
import json
import logging
import os
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib import parse, request
from urllib.error import HTTPError, URLError

from .config import IbkrConfig, RateLimitConfig
from .models import Instrument
from .rate_limiter import ExponentialBackoff, RequestPacer
from .time_utils import to_utc


class IbkrClientError(Exception):
    """Base IBKR client exception."""


class IbkrDependencyError(IbkrClientError):
    """Raised when Nautilus/IBKR runtime dependencies are unavailable."""


class IbkrConnectionError(IbkrClientError):
    """Raised when connection/session initialization fails."""


class IbkrRequestValidationError(IbkrClientError):
    """Raised when request payload is invalid."""


class IbkrUpstreamError(IbkrClientError):
    """Raised when upstream returns invalid data or unexpected payload."""


@dataclass(frozen=True)
class IbkrRuntimeConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1001
    account: str | None = None
    gateway_mode: str = "paper"
    connect_timeout_seconds: float = 10.0
    fallback_enabled: bool = True


@dataclass(frozen=True)
class HistoricalRequest:
    instrument: Instrument
    start_utc: datetime
    end_utc: datetime
    bar_size: str
    what_to_show: str
    use_regular_trading_hours: bool
    frequency: str = "daily"


class HistoricalBackend(Protocol):
    def get_head_timestamp(self, instrument: Instrument) -> datetime | None:
        ...

    def fetch_historical(self, request: HistoricalRequest) -> list[dict[str, Any]]:
        ...

    def fetch_market_snapshot(self, conids: list[str]) -> list[dict[str, Any]]:
        ...


class NautilusIbkrBackend:
    """IBKR Client Portal backend with Nautilus runtime validation.

    This backend uses Client Portal Gateway HTTP endpoints for symbol resolution
    and historical bars while still validating that NautilusTrader exists in
    the environment per project runtime expectations.
    """

    _MAX_HEAD_PAGES = 12
    _HEAD_PAGE_PERIOD = "5y"

    def __init__(self, runtime: IbkrRuntimeConfig, logger: logging.Logger | None = None):
        self.runtime = runtime
        self.logger = logger or logging.getLogger(__name__)
        self._nautilus_module = None
        self._conid_cache: dict[tuple[str, str, str], str] = {}
        self._ssl_context = ssl._create_unverified_context()
        self._base_url = self._build_base_url()
        self._bootstrap_runtime()

    def _bootstrap_runtime(self) -> None:
        try:
            self._nautilus_module = importlib.import_module("nautilus_trader")
        except ModuleNotFoundError as exc:
            raise IbkrDependencyError("nautilus_trader is not installed in the current environment.") from exc
        except Exception as exc:  # pragma: no cover - defensive for unexpected import states
            raise IbkrConnectionError(f"Failed loading nautilus runtime: {exc}") from exc

        # Optional hook for future concrete wiring. We intentionally make this
        # explicit now so unsupported runtime states fail loudly.
        if not hasattr(self._nautilus_module, "__version__"):
            raise IbkrDependencyError("nautilus_trader runtime is present but missing version metadata.")

        try:
            with socket.create_connection(
                (self.runtime.host, self.runtime.port),
                timeout=self.runtime.connect_timeout_seconds,
            ):
                pass
        except OSError as exc:
            raise IbkrConnectionError(
                f"Unable to reach IBKR endpoint at {self.runtime.host}:{self.runtime.port}. "
                "Start Client Portal Gateway and ensure the configured host/port are correct."
            ) from exc

        self._ensure_authenticated()

    def _build_base_url(self) -> str:
        host = self.runtime.host.strip()
        if host.startswith("http://") or host.startswith("https://"):
            parsed = parse.urlsplit(host)
            if parsed.netloc and parsed.port is not None:
                return host.rstrip("/")
            if parsed.netloc:
                netloc = parsed.netloc if self.runtime.port == 0 else f"{parsed.netloc}:{self.runtime.port}"
                return parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", "")).rstrip("/")
            return host.rstrip("/")

        scheme = "https" if self.runtime.port in (5000, 5001) else "http"
        return f"{scheme}://{host}:{self.runtime.port}"

    def _ensure_authenticated(self) -> None:
        try:
            payload = self._request_json("GET", "/v1/api/iserver/auth/status")
        except IbkrUpstreamError as exc:
            if "HTTP 401" in str(exc):
                raise IbkrConnectionError(
                    "Client Portal Gateway returned 401 Unauthorized. "
                    "Open the gateway web session and complete IBKR login first."
                ) from exc
            raise
        if not isinstance(payload, dict):
            raise IbkrConnectionError("Unexpected auth status payload from Client Portal Gateway.")

        is_authenticated = bool(payload.get("authenticated", False))
        is_connected = payload.get("connected", None)
        if not is_authenticated:
            raise IbkrConnectionError(
                "Client Portal Gateway is reachable but not authenticated. "
                "Complete IBKR login in the gateway UI, then retry."
            )
        if is_connected is False:
            raise IbkrConnectionError(
                "Client Portal Gateway is authenticated but not connected to broker backend."
            )

    def _resolve_contract(self, instrument: Instrument) -> dict[str, str]:
        if instrument.asset_class == "equity":
            return {"symbol": instrument.symbol, "exchange": instrument.exchange, "secType": "STK"}
        if instrument.asset_class == "index":
            return {"symbol": instrument.symbol, "exchange": instrument.exchange, "secType": "IND"}
        raise IbkrRequestValidationError(f"Unsupported asset_class: {instrument.asset_class}")

    def _resolve_conid(self, instrument: Instrument) -> str:
        key = (instrument.symbol, instrument.exchange, instrument.asset_class)
        cached = self._conid_cache.get(key)
        if cached:
            self.logger.debug(
                "Resolved conid from cache: symbol=%s, exchange=%s, asset_class=%s, conid=%s",
                instrument.symbol,
                instrument.exchange,
                instrument.asset_class,
                cached,
            )
            return cached

        self.logger.debug(
            "Resolving conid: symbol=%s, exchange=%s, asset_class=%s",
            instrument.symbol,
            instrument.exchange,
            instrument.asset_class,
        )

        contract = self._resolve_contract(instrument)
        payload = {
            "symbol": contract["symbol"],
            "name": True,
            "secType": contract["secType"],
        }
        response = self._request_json("POST", "/v1/api/iserver/secdef/search", json_payload=payload)
        if not isinstance(response, list):
            raise IbkrUpstreamError("Contract search response must be a list.")

        exchange_upper = instrument.exchange.upper()
        for candidate in response:
            if not isinstance(candidate, dict):
                continue
            conid = candidate.get("conid")
            if conid in (None, ""):
                continue

            listing_exchange = str(
                candidate.get("listingExchange")
                or candidate.get("exchange")
                or candidate.get("description")
                or ""
            ).upper()

            if exchange_upper and exchange_upper not in listing_exchange:
                # Keep searching until we find a best match for configured exchange.
                continue

            conid_str = str(conid)
            self._conid_cache[key] = conid_str
            self.logger.debug(
                "Resolved conid (exchange match): symbol=%s, exchange=%s, asset_class=%s, conid=%s",
                instrument.symbol,
                instrument.exchange,
                instrument.asset_class,
                conid_str,
            )
            return conid_str

        # Fallback to first valid result if exact exchange match was not returned.
        for candidate in response:
            if isinstance(candidate, dict) and candidate.get("conid") not in (None, ""):
                conid_str = str(candidate["conid"])
                self._conid_cache[key] = conid_str
                self.logger.debug(
                    "Resolved conid (fallback): symbol=%s, exchange=%s, asset_class=%s, conid=%s",
                    instrument.symbol,
                    instrument.exchange,
                    instrument.asset_class,
                    conid_str,
                )
                return conid_str

        raise IbkrUpstreamError(
            f"Unable to resolve conid for {instrument.symbol} on {instrument.exchange} ({instrument.asset_class})."
        )

    def _map_bar_size(self, bar_size: str) -> str:
        normalized = bar_size.strip().lower()
        if normalized in {"1 day", "1d", "day"}:
            return "1d"
        raise IbkrRequestValidationError(f"Unsupported bar_size for Client Portal history endpoint: {bar_size}")

    def _window_period(self, start_utc: datetime, end_utc: datetime) -> str:
        span_days = max(1, int((end_utc - start_utc).total_seconds() // 86400) + 1)
        if span_days >= 365:
            years = max(1, span_days // 365)
            return f"{years}y"
        return f"{span_days}d"

    def _fetch_history(
        self,
        conid: str,
        period: str,
        bar_size: str,
        use_regular_trading_hours: bool,
        end_utc: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "conid": conid,
            "period": period,
            "bar": self._map_bar_size(bar_size),
            "outsideRth": str(not use_regular_trading_hours).lower(),
        }
        if end_utc is not None:
            params["startTime"] = end_utc.strftime("%Y%m%d-%H:%M:%S")

        self.logger.debug(
            "Fetching history: conid=%s, period=%s, bar_size=%s, use_rth=%s, end_utc=%s",
            conid,
            period,
            bar_size,
            use_regular_trading_hours,
            end_utc.isoformat() if end_utc else None,
        )

        payload = self._request_json("GET", "/v1/api/iserver/marketdata/history", params=params)
        if not isinstance(payload, dict):
            raise IbkrUpstreamError("History response must be a JSON object.")

        if payload.get("error"):
            raise IbkrUpstreamError(f"IBKR history error: {payload['error']}")

        rows = payload.get("data", [])
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise IbkrUpstreamError("History response field 'data' must be a list.")

        self.logger.debug(
            "History fetch success: conid=%s, period=%s, rows_returned=%d",
            conid,
            period,
            len(rows),
        )
        return rows

    def _extract_timestamp(self, row: dict[str, Any]) -> datetime:
        raw = row.get("t") or row.get("time") or row.get("timestamp")
        if raw is None:
            raise IbkrUpstreamError("History row missing timestamp field.")
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw) / 1000.0, tz=timezone.utc)
        if isinstance(raw, str):
            cleaned = raw.strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(cleaned)
            except ValueError as exc:
                raise IbkrUpstreamError(f"Unable to parse IBKR timestamp: {raw}") from exc
            return to_utc(parsed)
        raise IbkrUpstreamError(f"Unsupported history timestamp type: {type(raw)}")

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> Any:
        endpoint = f"{self._base_url}{path}"
        if params:
            endpoint = f"{endpoint}?{parse.urlencode(params)}"

        body: bytes | None = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if json_payload is not None:
            body = json.dumps(json_payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        self.logger.debug(
            "IBKR request: method=%s, path=%s, params=%s, payload=%s",
            method.upper(),
            path,
            params,
            json_payload,
        )

        req = request.Request(endpoint, method=method.upper(), headers=headers, data=body)

        try:
            with request.urlopen(req, timeout=self.runtime.connect_timeout_seconds, context=self._ssl_context) as resp:
                response_bytes = resp.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise IbkrUpstreamError(f"IBKR HTTP {exc.code} at {path}: {detail}") from exc
        except URLError as exc:
            raise IbkrConnectionError(f"Unable to call Client Portal endpoint {path}: {exc}") from exc
        except TimeoutError as exc:
            raise IbkrConnectionError(f"Timeout calling Client Portal endpoint {path}") from exc

        if not response_bytes:
            return {}

        try:
            return json.loads(response_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise IbkrUpstreamError(f"Invalid JSON from Client Portal endpoint {path}") from exc

    def get_head_timestamp(self, instrument: Instrument) -> datetime | None:
        conid = self._resolve_conid(instrument)
        cursor = datetime.now(timezone.utc)
        oldest: datetime | None = None

        for page_num in range(self._MAX_HEAD_PAGES):
            self.logger.debug(
                "Head timestamp page fetch: symbol=%s, exchange=%s, page=%d, cursor=%s",
                instrument.symbol,
                instrument.exchange,
                page_num + 1,
                cursor.isoformat(),
            )
            rows = self._fetch_history(
                conid=conid,
                period=self._HEAD_PAGE_PERIOD,
                bar_size="1 day",
                use_regular_trading_hours=True,
                end_utc=cursor,
            )
            if not rows:
                self.logger.debug(
                    "Head timestamp page empty: symbol=%s, exchange=%s, page=%d",
                    instrument.symbol,
                    instrument.exchange,
                    page_num + 1,
                )
                break

            timestamps = [self._extract_timestamp(row) for row in rows if isinstance(row, dict)]
            if not timestamps:
                break
            chunk_oldest = min(timestamps)
            oldest = chunk_oldest if oldest is None else min(oldest, chunk_oldest)

            self.logger.debug(
                "Head timestamp page result: symbol=%s, exchange=%s, page=%d, chunk_oldest=%s, oldest=%s",
                instrument.symbol,
                instrument.exchange,
                page_num + 1,
                chunk_oldest.isoformat(),
                oldest.isoformat() if oldest else None,
            )

            next_cursor = chunk_oldest - timedelta(days=1)
            if next_cursor >= cursor:
                break
            cursor = next_cursor

        return oldest

    def fetch_historical(self, request: HistoricalRequest) -> list[dict[str, Any]]:
        conid = self._resolve_conid(request.instrument)
        period = self._window_period(request.start_utc, request.end_utc)
        rows = self._fetch_history(
            conid=conid,
            period=period,
            bar_size=request.bar_size,
            use_regular_trading_hours=request.use_regular_trading_hours,
            end_utc=request.end_utc,
        )

        filtered: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts = self._extract_timestamp(row)
            if ts < request.start_utc or ts > request.end_utc:
                continue
            filtered.append(
                {
                    "timestamp": ts,
                    "open": row.get("o", row.get("open", 0.0)),
                    "high": row.get("h", row.get("high", 0.0)),
                    "low": row.get("l", row.get("low", 0.0)),
                    "close": row.get("c", row.get("close", 0.0)),
                    "volume": row.get("v", row.get("volume", 0.0)),
                }
            )
        return filtered

    def fetch_market_snapshot(self, conids: list[str]) -> list[dict[str, Any]]:
        if not conids:
            return []

        conids_str = ",".join(conids)
        self.logger.debug(
            "Fetching market snapshot: conids=%s, count=%d",
            conids_str,
            len(conids),
        )

        params: dict[str, str] = {"conids": conids_str}
        payload = self._request_json("GET", "/v1/api/iserver/marketdata/snapshot", params=params)
        if not isinstance(payload, dict):
            raise IbkrUpstreamError("Market snapshot response must be a JSON object.")

        if payload.get("error"):
            raise IbkrUpstreamError(f"IBKR market snapshot error: {payload['error']}")

        rows = payload.get("data", [])
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise IbkrUpstreamError("Market snapshot response field 'data' must be a list.")

        self.logger.debug(
            "Market snapshot fetch success: conids=%s, rows_returned=%d",
            conids_str,
            len(rows),
        )
        return rows


class IbkrHistoricalClient:
    def __init__(
        self,
        runtime: IbkrRuntimeConfig,
        rate_limits: RateLimitConfig | None = None,
        backend: HistoricalBackend | None = None,
        logger: logging.Logger | None = None,
        pacer: RequestPacer | None = None,
        backoff: ExponentialBackoff | None = None,
    ):
        self.runtime = runtime
        self.logger = logger or logging.getLogger(__name__)
        self.rate_limits = rate_limits or RateLimitConfig()
        self._backend = backend
        self._pacer = pacer or RequestPacer(
            max_requests_per_window=self.rate_limits.max_requests_per_window,
            window_seconds=self.rate_limits.window_seconds,
            identical_request_cooldown_seconds=self.rate_limits.identical_request_cooldown_seconds,
            utilization_target_pct=self.rate_limits.utilization_target_pct,
            base_delay_seconds=self.rate_limits.base_delay_seconds,
            jitter_seconds=self.rate_limits.jitter_seconds,
        )
        self._backoff = backoff or ExponentialBackoff(
            max_retries=self.rate_limits.max_retries,
            base_delay_seconds=self.rate_limits.backoff_base_seconds,
            max_backoff_seconds=self.rate_limits.max_backoff_seconds,
            jitter_seconds=self.rate_limits.backoff_jitter_seconds,
        )

        if self._backend is None:
            self._backend = self._build_backend()

    @classmethod
    def from_ibkr_config(
        cls,
        cfg: IbkrConfig,
        rate_limits: RateLimitConfig | None = None,
        backend: HistoricalBackend | None = None,
        logger: logging.Logger | None = None,
    ) -> "IbkrHistoricalClient":
        runtime = IbkrRuntimeConfig(
            host=cfg.host,
            port=cfg.port,
            client_id=cfg.client_id,
            account=cfg.account,
            gateway_mode=cfg.gateway_mode,
            connect_timeout_seconds=cfg.connect_timeout_seconds,
            fallback_enabled=cfg.fallback_enabled,
        )
        return cls(runtime=runtime, rate_limits=rate_limits, backend=backend, logger=logger)

    @classmethod
    def from_env(
        cls,
        fallback_enabled: bool = True,
        rate_limits: RateLimitConfig | None = None,
        backend: HistoricalBackend | None = None,
        logger: logging.Logger | None = None,
    ) -> "IbkrHistoricalClient":
        runtime = IbkrRuntimeConfig(
            host=os.getenv("IBKR_HOST", "127.0.0.1"),
            port=int(os.getenv("IBKR_PORT", "7497")),
            client_id=int(os.getenv("IBKR_CLIENT_ID", "1001")),
            account=os.getenv("IBKR_ACCOUNT"),
            gateway_mode=os.getenv("TRADING_MODE", "paper"),
            connect_timeout_seconds=float(os.getenv("IBKR_CONNECT_TIMEOUT_SECONDS", "10.0")),
            fallback_enabled=fallback_enabled,
        )
        return cls(runtime=runtime, rate_limits=rate_limits, backend=backend, logger=logger)

    def _build_backend(self) -> HistoricalBackend:
        try:
            return NautilusIbkrBackend(runtime=self.runtime, logger=self.logger)
        except (IbkrDependencyError, IbkrConnectionError) as exc:
            if self.runtime.fallback_enabled:
                self.logger.warning("IBKR/Nautilus backend unavailable. Fallback mode enabled. reason=%s", exc)
                return _FallbackBackend()
            raise

    def _validate_utc(self, value: datetime, field_name: str) -> datetime:
        if value.tzinfo is None:
            raise IbkrRequestValidationError(f"{field_name} must be timezone-aware UTC datetime.")
        return to_utc(value)

    def _validate_request(self, request: HistoricalRequest) -> HistoricalRequest:
        start = self._validate_utc(request.start_utc, "start_utc")
        end = self._validate_utc(request.end_utc, "end_utc")
        if start >= end:
            raise IbkrRequestValidationError("start_utc must be earlier than end_utc.")

        return HistoricalRequest(
            instrument=request.instrument,
            start_utc=start,
            end_utc=end,
            bar_size=request.bar_size,
            what_to_show=request.what_to_show,
            use_regular_trading_hours=request.use_regular_trading_hours,
            frequency=request.frequency,
        )

    def get_head_timestamp(self, instrument: Instrument) -> datetime | None:
        self.logger.debug(
            "Get head timestamp request: symbol=%s, exchange=%s, asset_class=%s",
            instrument.symbol,
            instrument.exchange,
            instrument.asset_class,
        )
        self._pacer.pace(self._head_request_key(instrument))
        head = self._backoff.run(
            lambda: self._backend.get_head_timestamp(instrument),
            self._is_retryable_error,
        )
        if head is None:
            self.logger.debug(
                "Get head timestamp response: symbol=%s, exchange=%s, head=None",
                instrument.symbol,
                instrument.exchange,
            )
            return None
        if not isinstance(head, datetime):
            raise IbkrUpstreamError("Head timestamp from backend is not a datetime.")
        head_utc = to_utc(head)
        self.logger.debug(
            "Get head timestamp response: symbol=%s, exchange=%s, head=%s",
            instrument.symbol,
            instrument.exchange,
            head_utc.isoformat(),
        )
        return head_utc

    def fetch_bars(self, request: HistoricalRequest) -> list[dict]:
        validated = self._validate_request(request)
        self.logger.debug(
            "Fetch bars request: symbol=%s, exchange=%s, asset_class=%s, frequency=%s, bar_size=%s, "
            "what_to_show=%s, use_rth=%s, start_utc=%s, end_utc=%s",
            validated.instrument.symbol,
            validated.instrument.exchange,
            validated.instrument.asset_class,
            validated.frequency,
            validated.bar_size,
            validated.what_to_show,
            validated.use_regular_trading_hours,
            validated.start_utc.isoformat(),
            validated.end_utc.isoformat(),
        )
        self._pacer.pace(self._historical_request_key(validated))
        raw_rows = self._backoff.run(
            lambda: self._backend.fetch_historical(validated),
            self._is_retryable_error,
        )
        if not isinstance(raw_rows, list):
            raise IbkrUpstreamError("Historical response must be a list of dict rows.")
        normalized = self._normalize_rows(raw_rows, validated)
        self.logger.debug(
            "Fetch bars response: symbol=%s, exchange=%s, rows_normalized=%d",
            validated.instrument.symbol,
            validated.instrument.exchange,
            len(normalized),
        )
        return normalized

    def _is_retryable_error(self, exc: Exception) -> bool:
        return isinstance(exc, (IbkrConnectionError, IbkrUpstreamError, TimeoutError, OSError))

    def _head_request_key(self, instrument: Instrument) -> str:
        return f"head:{instrument.exchange}:{instrument.asset_class}:{instrument.symbol}"

    def _historical_request_key(self, request: HistoricalRequest) -> str:
        return (
            f"hist:{request.instrument.exchange}:{request.instrument.asset_class}:{request.instrument.symbol}:"
            f"{request.frequency}:{request.bar_size}:{request.what_to_show}:{int(request.use_regular_trading_hours)}:"
            f"{request.start_utc.isoformat()}:{request.end_utc.isoformat()}"
        )

    def _normalize_rows(self, rows: list[dict[str, Any]], request: HistoricalRequest) -> list[dict]:
        normalized: list[dict[str, Any]] = []

        for row in rows:
            if not isinstance(row, dict):
                raise IbkrUpstreamError("Historical row must be a dict.")

            ts = row.get("timestamp") or row.get("ts") or row.get("time")
            if ts is None:
                raise IbkrUpstreamError("Historical row missing timestamp field.")

            parsed_ts = self._parse_timestamp(ts)

            normalized.append(
                {
                    "symbol": request.instrument.symbol,
                    "exchange": request.instrument.exchange,
                    "asset_class": request.instrument.asset_class,
                    "frequency": request.frequency,
                    "timestamp": parsed_ts,
                    "open": float(row.get("open", row.get("o", 0.0))),
                    "high": float(row.get("high", row.get("h", 0.0))),
                    "low": float(row.get("low", row.get("l", 0.0))),
                    "close": float(row.get("close", row.get("c", 0.0))),
                    "volume": float(row.get("volume", row.get("v", 0.0))),
                }
            )

        deduped: dict[datetime, dict] = {}
        for row in normalized:
            deduped[row["timestamp"]] = row

        return [deduped[k] for k in sorted(deduped.keys())]

    def _parse_timestamp(self, ts: Any) -> datetime:
        if isinstance(ts, datetime):
            return to_utc(ts)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(ts, str):
            cleaned = ts.strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(cleaned)
            except ValueError as exc:
                raise IbkrUpstreamError(f"Unable to parse timestamp string: {ts}") from exc
            return to_utc(parsed)
        raise IbkrUpstreamError(f"Unsupported timestamp type: {type(ts)}")

    def fetch_market_snapshot(self, instruments: list[Instrument]) -> list[dict]:
        if not instruments:
            return []

        symbols = ", ".join(f"{inst.symbol}@{inst.exchange}" for inst in instruments)
        self.logger.debug(
            "Fetch market snapshot request: instruments=%s, count=%d",
            symbols,
            len(instruments),
        )

        conids = []
        for instrument in instruments:
            try:
                conid = self._backend._resolve_conid(instrument)
                conids.append(conid)
            except IbkrClientError as exc:
                self.logger.warning(
                    "Failed to resolve conid for snapshot: symbol=%s, exchange=%s, error=%s",
                    instrument.symbol,
                    instrument.exchange,
                    str(exc),
                )

        if not conids:
            return []

        self._pacer.pace(f"snapshot:{','.join(conids)}")
        raw_rows = self._backoff.run(
            lambda: self._backend.fetch_market_snapshot(conids),
            self._is_retryable_error,
        )
        if not isinstance(raw_rows, list):
            raise IbkrUpstreamError("Market snapshot response must be a list of dict rows.")

        self.logger.debug(
            "Fetch market snapshot response: instruments=%s, rows_returned=%d",
            symbols,
            len(raw_rows),
        )
        return raw_rows


class _FallbackBackend:
    def get_head_timestamp(self, instrument: Instrument) -> datetime | None:
        _ = instrument
        return None

    def fetch_historical(self, request: HistoricalRequest) -> list[dict[str, Any]]:
        _ = request
        return []

    def fetch_market_snapshot(self, conids: list[str]) -> list[dict[str, Any]]:
        _ = conids
        return []
