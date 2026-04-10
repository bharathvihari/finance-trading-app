from __future__ import annotations

import importlib
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

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


class NautilusIbkrBackend:
    """Nautilus-backed backend with version-tolerant dynamic loading.

    This intentionally avoids hard-coding unstable Nautilus APIs. It verifies
    availability at runtime and can be switched to concrete methods later.
    """

    def __init__(self, runtime: IbkrRuntimeConfig):
        self.runtime = runtime
        self._nautilus_module = None
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
                f"Unable to reach IBKR gateway at {self.runtime.host}:{self.runtime.port}. "
                "Start IBKR Gateway/TWS or enable fallback mode."
            ) from exc

    def _resolve_contract(self, instrument: Instrument) -> dict[str, str]:
        if instrument.asset_class == "equity":
            return {"symbol": instrument.symbol, "exchange": instrument.exchange, "secType": "STK"}
        if instrument.asset_class == "index":
            return {"symbol": instrument.symbol, "exchange": instrument.exchange, "secType": "IND"}
        raise IbkrRequestValidationError(f"Unsupported asset_class: {instrument.asset_class}")

    def get_head_timestamp(self, instrument: Instrument) -> datetime | None:
        # Concrete IBKR/Nautilus call should be wired in Step 8 orchestration.
        # For now this backend confirms runtime readiness and exposes a clear contract.
        _ = self._resolve_contract(instrument)
        return None

    def fetch_historical(self, request: HistoricalRequest) -> list[dict[str, Any]]:
        _ = self._resolve_contract(request.instrument)
        return []


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
            return NautilusIbkrBackend(runtime=self.runtime)
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
        self._pacer.pace(self._head_request_key(instrument))
        head = self._backoff.run(
            lambda: self._backend.get_head_timestamp(instrument),
            self._is_retryable_error,
        )
        if head is None:
            return None
        if not isinstance(head, datetime):
            raise IbkrUpstreamError("Head timestamp from backend is not a datetime.")
        return to_utc(head)

    def fetch_bars(self, request: HistoricalRequest) -> list[dict]:
        validated = self._validate_request(request)
        self._pacer.pace(self._historical_request_key(validated))
        raw_rows = self._backoff.run(
            lambda: self._backend.fetch_historical(validated),
            self._is_retryable_error,
        )
        if not isinstance(raw_rows, list):
            raise IbkrUpstreamError("Historical response must be a list of dict rows.")
        return self._normalize_rows(raw_rows, validated)

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


class _FallbackBackend:
    def get_head_timestamp(self, instrument: Instrument) -> datetime | None:
        _ = instrument
        return None

    def fetch_historical(self, request: HistoricalRequest) -> list[dict[str, Any]]:
        _ = request
        return []
