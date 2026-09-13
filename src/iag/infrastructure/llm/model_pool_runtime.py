"""Runtime selection, health memory, and conservative endpoint failover."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, TypeVar

from iag.infrastructure.llm.endpoint_probe import (
    EndpointProbeResult,
    probe_endpoint,
)
from iag.infrastructure.llm.model_pool import ModelEndpoint, ModelPool
from iag.infrastructure.llm.providers import LLMConnectionError, LLMTimeoutError


ResultT = TypeVar("ResultT")
ProviderFilter = str | Collection[str] | None


def _provider_matches(provider: str, expected: ProviderFilter) -> bool:
    if expected is None:
        return True
    if isinstance(expected, str):
        return provider == expected
    return provider in expected


@dataclass(frozen=True, slots=True)
class RequestFailure:
    """A routing decision derived from one provider exception."""

    status: str
    detail: str
    safe_to_failover: bool
    cooldown_seconds: int
    status_code: int | None = None


@dataclass(slots=True)
class EndpointHealth:
    status: str = "unknown"
    detail: str = "Not checked yet."
    last_checked_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    cooldown_until_epoch: float | None = None
    consecutive_failures: int = 0
    http_status: int | None = None
    model_found: bool | None = None
    discovered_model_count: int = 0
    retry_after_seconds: int | None = None
    probe_url: str | None = None


class ModelPoolExhaustedError(RuntimeError):
    """No eligible endpoint completed a model operation."""

    def __init__(self, pool_id: str, attempts: list[dict[str, Any]]) -> None:
        self.pool_id = pool_id
        self.attempts = attempts
        summary = ", ".join(
            f"{item['endpoint_id']}={item['status']}" for item in attempts
        ) or "no eligible endpoint"
        super().__init__(f"Model pool {pool_id!r} is exhausted: {summary}")


def _utc_iso(epoch: float | None = None) -> str:
    value = time.time() if epoch is None else epoch
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def _status_code(error: BaseException) -> int | None:
    direct = getattr(error, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _retry_after(error: BaseException) -> int | None:
    direct = getattr(error, "retry_after_seconds", None)
    if isinstance(direct, int):
        return max(0, direct)
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    text = str(raw or "").strip()
    return max(0, int(text)) if text.isdigit() else None


def classify_request_error(
    error: BaseException,
    endpoint: ModelEndpoint,
) -> RequestFailure:
    """Classify only failures whose replay safety is reasonably knowable."""
    code = _status_code(error)
    detail = f"{type(error).__name__}: {error}"
    if code in {401, 403}:
        return RequestFailure(
            "authentication_failed", detail, True, 300, code
        )
    if code == 404:
        return RequestFailure("model_unavailable", detail, True, 300, code)
    if code == 429:
        cooldown = _retry_after(error) or endpoint.rate_limit_cooldown_seconds
        return RequestFailure("rate_limited", detail, True, cooldown, code)
    if code is not None and code >= 500:
        # The provider may have accepted the request before returning 5xx.
        return RequestFailure(
            "temporarily_unhealthy", detail, False, 30, code
        )
    if isinstance(error, LLMTimeoutError) or type(error).__name__ == "APITimeoutError":
        return RequestFailure("temporarily_unhealthy", detail, False, 30, code)
    if isinstance(error, LLMConnectionError) or (
        code is None
        and type(error).__name__ in {
            "APIConnectionError",
            "ConnectError",
            "ConnectionError",
        }
    ):
        return RequestFailure("unreachable", detail, True, 30, code)
    if code is not None:
        return RequestFailure("request_rejected", detail, False, 0, code)
    return RequestFailure("unknown_error", detail, False, 0, None)


class ModelPoolRuntime:
    """Own transient endpoint state while persisted configuration stays static."""

    def __init__(
        self,
        pool: ModelPool,
        *,
        probe_function: Callable[[ModelEndpoint], EndpointProbeResult] = probe_endpoint,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._lock = RLock()
        self._probe_function = probe_function
        self._clock = clock
        self._pool = self._validate(pool)
        self._health = {
            endpoint.endpoint_id: EndpointHealth()
            for endpoint in self._pool.endpoints
        }
        self._last_selected_endpoint_id: str | None = None

    @staticmethod
    def _validate(pool: ModelPool) -> ModelPool:
        return ModelPool.model_validate(
            pool.model_dump(mode="python")
        ).model_copy(deep=True)

    def replace_pool(
        self,
        pool: ModelPool,
        *,
        reset_health: bool = False,
    ) -> None:
        validated = self._validate(pool)
        with self._lock:
            previous = self._health
            self._pool = validated
            self._health = {
                endpoint.endpoint_id: (
                    EndpointHealth()
                    if reset_health
                    else previous.get(endpoint.endpoint_id, EndpointHealth())
                )
                for endpoint in validated.endpoints
            }
            if self._last_selected_endpoint_id not in self._health:
                self._last_selected_endpoint_id = None

    def pool(self) -> ModelPool:
        with self._lock:
            return self._pool.model_copy(deep=True)

    def candidates(
        self,
        *,
        provider: ProviderFilter = None,
        require_tools: bool = False,
    ) -> tuple[ModelEndpoint, ...]:
        now = self._clock()
        with self._lock:
            return tuple(
                endpoint.model_copy(deep=True)
                for _, endpoint in sorted(
                    enumerate(self._pool.endpoints),
                    key=lambda item: (item[1].priority, item[0]),
                )
                if endpoint.enabled
                and _provider_matches(endpoint.provider, provider)
                and (not require_tools or endpoint.supports_tools)
                and not (
                    self._health[endpoint.endpoint_id].cooldown_until_epoch
                    and self._health[endpoint.endpoint_id].cooldown_until_epoch > now
                )
            )

    def preferred_endpoint(
        self,
        *,
        provider: ProviderFilter = None,
        require_tools: bool = False,
    ) -> ModelEndpoint:
        candidates = self.candidates(
            provider=provider,
            require_tools=require_tools,
        )
        if not candidates:
            raise ModelPoolExhaustedError(self._pool.pool_id, [])
        return candidates[0]

    def context_endpoint(
        self,
        *,
        provider: ProviderFilter = None,
        require_tools: bool = False,
    ) -> ModelEndpoint:
        """Use the smallest eligible context limits so fallback remains valid."""
        candidates = self.candidates(
            provider=provider,
            require_tools=require_tools,
        )
        if not candidates:
            raise ModelPoolExhaustedError(self._pool.pool_id, [])
        maximum = min(item.model_context_window_tokens for item in candidates)
        reserve = min(item.max_output_tokens for item in candidates)
        return candidates[0].model_copy(
            update={
                "model_context_window_tokens": maximum,
                "max_output_tokens": min(reserve, maximum),
            },
            deep=True,
        )

    def _record_failure(
        self,
        endpoint: ModelEndpoint,
        failure: RequestFailure,
    ) -> None:
        now = self._clock()
        with self._lock:
            health = self._health[endpoint.endpoint_id]
            health.status = failure.status
            health.detail = failure.detail[:1000]
            health.last_checked_at = _utc_iso(now)
            health.last_failure_at = _utc_iso(now)
            health.consecutive_failures += 1
            health.http_status = failure.status_code
            health.retry_after_seconds = (
                failure.cooldown_seconds if failure.cooldown_seconds else None
            )
            health.cooldown_until_epoch = (
                now + failure.cooldown_seconds
                if failure.cooldown_seconds
                else None
            )

    def _record_success(self, endpoint: ModelEndpoint) -> None:
        now = self._clock()
        with self._lock:
            health = self._health[endpoint.endpoint_id]
            health.status = "available"
            health.detail = "The latest model request completed successfully."
            health.last_checked_at = _utc_iso(now)
            health.last_success_at = _utc_iso(now)
            health.cooldown_until_epoch = None
            health.consecutive_failures = 0
            health.http_status = 200
            health.retry_after_seconds = None
            self._last_selected_endpoint_id = endpoint.endpoint_id

    def execute(
        self,
        operation: Callable[[ModelEndpoint], ResultT],
        *,
        provider: ProviderFilter = None,
        require_tools: bool = False,
    ) -> ResultT:
        """Try endpoints by priority; replay only explicitly safe failures."""
        attempts: list[dict[str, Any]] = []
        candidates = self.candidates(
            provider=provider,
            require_tools=require_tools,
        )
        if not candidates:
            raise ModelPoolExhaustedError(self._pool.pool_id, attempts)
        for endpoint in candidates:
            try:
                result = operation(endpoint)
            except Exception as error:
                failure = classify_request_error(error, endpoint)
                self._record_failure(endpoint, failure)
                attempts.append(
                    {
                        "endpoint_id": endpoint.endpoint_id,
                        "status": failure.status,
                        "status_code": failure.status_code,
                    }
                )
                if not failure.safe_to_failover:
                    raise
            else:
                self._record_success(endpoint)
                return result
        raise ModelPoolExhaustedError(self._pool.pool_id, attempts)

    def _apply_probe(self, result: EndpointProbeResult) -> None:
        now = self._clock()
        with self._lock:
            health = self._health[result.endpoint_id]
            health.status = result.status
            health.detail = result.detail[:1000]
            health.last_checked_at = result.checked_at
            health.http_status = result.http_status
            health.model_found = result.model_found
            health.discovered_model_count = result.discovered_model_count
            health.retry_after_seconds = result.retry_after_seconds
            health.probe_url = result.probe_url
            cooldown = 0
            if result.status == "rate_limited":
                endpoint = next(
                    item
                    for item in self._pool.endpoints
                    if item.endpoint_id == result.endpoint_id
                )
                cooldown = (
                    result.retry_after_seconds
                    or endpoint.rate_limit_cooldown_seconds
                )
            elif result.status == "authentication_failed":
                cooldown = 300
            elif result.status in {"temporarily_unhealthy", "unreachable"}:
                cooldown = 30
            health.cooldown_until_epoch = now + cooldown if cooldown else None
            if result.status == "available":
                health.consecutive_failures = 0
            elif result.status not in {
                "reachable_unconfirmed",
                "probe_unsupported",
            }:
                health.consecutive_failures += 1

    def probe(self, endpoint_id: str) -> dict[str, Any]:
        with self._lock:
            endpoint = next(
                (
                    item.model_copy(deep=True)
                    for item in self._pool.endpoints
                    if item.endpoint_id == endpoint_id
                ),
                None,
            )
        if endpoint is None:
            raise KeyError(f"Unknown endpoint_id: {endpoint_id}")
        result = self._probe_function(endpoint)
        self._apply_probe(result)
        return result.as_dict()

    def probe_all(self) -> list[dict[str, Any]]:
        with self._lock:
            endpoint_ids = [
                item.endpoint_id for item in self._pool.endpoints if item.enabled
            ]
        return [self.probe(endpoint_id) for endpoint_id in endpoint_ids]

    def status(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            endpoints = []
            for endpoint in sorted(
                self._pool.endpoints,
                key=lambda item: item.priority,
            ):
                health = self._health[endpoint.endpoint_id]
                cooldown_until = health.cooldown_until_epoch
                endpoints.append(
                    {
                        "endpoint_id": endpoint.endpoint_id,
                        "priority": endpoint.priority,
                        "enabled": endpoint.enabled,
                        "status": health.status,
                        "detail": health.detail,
                        "last_checked_at": health.last_checked_at,
                        "last_success_at": health.last_success_at,
                        "last_failure_at": health.last_failure_at,
                        "consecutive_failures": health.consecutive_failures,
                        "http_status": health.http_status,
                        "model_found": health.model_found,
                        "discovered_model_count": health.discovered_model_count,
                        "retry_after_seconds": health.retry_after_seconds,
                        "probe_url": health.probe_url,
                        "cooldown_until": (
                            _utc_iso(cooldown_until)
                            if cooldown_until and cooldown_until > now
                            else None
                        ),
                        "eligible": bool(
                            endpoint.enabled
                            and not (cooldown_until and cooldown_until > now)
                        ),
                    }
                )
            return {
                "pool_id": self._pool.pool_id,
                "last_selected_endpoint_id": self._last_selected_endpoint_id,
                "endpoints": endpoints,
            }
