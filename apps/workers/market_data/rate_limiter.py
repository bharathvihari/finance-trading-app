from __future__ import annotations

from collections import deque
from random import random
from time import monotonic, sleep
from typing import Callable


class RequestPacer:
    """IBKR historical-request pacing guard with safety utilization + jitter."""

    def __init__(
        self,
        max_requests_per_window: int,
        window_seconds: int,
        identical_request_cooldown_seconds: int,
        utilization_target_pct: int = 65,
        base_delay_seconds: float = 0.8,
        jitter_seconds: float = 0.6,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_requests_per_window <= 0 or window_seconds <= 0:
            raise ValueError("max_requests_per_window and window_seconds must be positive.")
        if identical_request_cooldown_seconds < 0:
            raise ValueError("identical_request_cooldown_seconds must be >= 0.")
        if not (1 <= utilization_target_pct <= 100):
            raise ValueError("utilization_target_pct must be within [1, 100].")

        self.max_requests_per_window = max_requests_per_window
        self.window_seconds = window_seconds
        self.identical_request_cooldown_seconds = identical_request_cooldown_seconds
        self.utilization_target_pct = utilization_target_pct
        self.base_delay_seconds = max(0.0, base_delay_seconds)
        self.jitter_seconds = max(0.0, jitter_seconds)
        self.clock = clock
        self.sleeper = sleeper

        self._events: deque[float] = deque()
        self._last_by_key: dict[str, float] = {}

    @property
    def effective_window_limit(self) -> int:
        value = int(self.max_requests_per_window * (self.utilization_target_pct / 100))
        return max(1, value)

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0] > self.window_seconds:
            self._events.popleft()

    def next_wait_seconds(self, request_key: str) -> float:
        now = self.clock()
        self._prune(now)

        wait_for_window = 0.0
        if len(self._events) >= self.effective_window_limit:
            wait_for_window = self.window_seconds - (now - self._events[0])

        wait_for_identical = 0.0
        if request_key in self._last_by_key:
            elapsed = now - self._last_by_key[request_key]
            if elapsed < self.identical_request_cooldown_seconds:
                wait_for_identical = self.identical_request_cooldown_seconds - elapsed

        jitter_wait = self.base_delay_seconds + (random() * self.jitter_seconds)
        return max(0.0, wait_for_window, wait_for_identical, jitter_wait)

    def pace(self, request_key: str) -> float:
        wait = self.next_wait_seconds(request_key=request_key)
        if wait > 0:
            self.sleeper(wait)
        mark_time = self.clock()
        self._prune(mark_time)
        self._events.append(mark_time)
        self._last_by_key[request_key] = mark_time
        return wait


class ExponentialBackoff:
    """Simple retry helper for transient upstream and connection issues."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        jitter_seconds: float = 0.5,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0.")
        self.max_retries = max_retries
        self.base_delay_seconds = max(0.0, base_delay_seconds)
        self.max_backoff_seconds = max(0.0, max_backoff_seconds)
        self.jitter_seconds = max(0.0, jitter_seconds)
        self.sleeper = sleeper

    def _delay_for_attempt(self, retry_attempt: int) -> float:
        base = self.base_delay_seconds * (2**retry_attempt)
        capped = min(base, self.max_backoff_seconds) if self.max_backoff_seconds > 0 else base
        return capped + (random() * self.jitter_seconds)

    def run(self, fn: Callable[[], object], is_retryable: Callable[[Exception], bool]) -> object:
        attempt = 0
        while True:
            try:
                return fn()
            except Exception as exc:
                if not is_retryable(exc) or attempt >= self.max_retries:
                    raise
                delay = self._delay_for_attempt(attempt)
                if delay > 0:
                    self.sleeper(delay)
                attempt += 1
