"""Read-only reachability checks for OpenAI-compatible model endpoints."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Literal

from iag.infrastructure.llm.model_pool import ModelEndpoint
from iag.infrastructure.llm.providers import api_headers, api_url


ProbeStatus = Literal[
    "available",
    "reachable_unconfirmed",
    "authentication_failed",
    "rate_limited",
    "probe_unsupported",
    "temporarily_unhealthy",
    "unreachable",
]


@dataclass(frozen=True, slots=True)
class EndpointProbeResult:
    """One bounded, secret-free observation from an endpoint models route."""

    endpoint_id: str
    status: ProbeStatus
    detail: str
    checked_at: str
    http_status: int | None = None
    model_found: bool | None = None
    discovered_model_count: int = 0
    discovered_model_ids: tuple[str, ...] = ()
    selected_model_metadata: dict[str, Any] | None = None
    retry_after_seconds: int | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["discovered_model_ids"] = list(self.discovered_model_ids)
        return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _retry_after_seconds(headers: Any) -> int | None:
    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    text = str(raw).strip()
    if text.isdigit():
        return max(0, int(text))
    try:
        retry_at = parsedate_to_datetime(text)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0, int((retry_at - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def _bounded_detail(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    return text[:500] if text else "The endpoint returned no error detail."


def _model_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        return []
    return [item for item in value["data"] if isinstance(item, dict)]


def _selected_metadata(record: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "canonical_slug",
        "context_length",
        "created",
        "default_parameters",
        "expiration_date",
        "object",
        "owned_by",
        "pricing",
        "reasoning",
        "supported_parameters",
        "top_provider",
    }
    return {key: record[key] for key in allowed if key in record}


def _http_failure(
    endpoint: ModelEndpoint,
    error: urllib.error.HTTPError,
) -> EndpointProbeResult:
    status_code = int(error.code)
    retry_after = _retry_after_seconds(error.headers)
    if status_code in {401, 403}:
        status: ProbeStatus = "authentication_failed"
    elif status_code == 429:
        status = "rate_limited"
    elif status_code in {404, 405, 501}:
        status = "probe_unsupported"
    elif status_code >= 500:
        status = "temporarily_unhealthy"
    else:
        status = "reachable_unconfirmed"
    return EndpointProbeResult(
        endpoint_id=endpoint.endpoint_id,
        status=status,
        detail=_bounded_detail(error.read()),
        checked_at=_now_iso(),
        http_status=status_code,
        retry_after_seconds=retry_after,
    )


def probe_endpoint(
    endpoint: ModelEndpoint,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> EndpointProbeResult:
    """GET the configured models route without creating a chat completion."""
    if endpoint.models_path is None:
        return EndpointProbeResult(
            endpoint_id=endpoint.endpoint_id,
            status="probe_unsupported",
            detail="Models probing is disabled for this endpoint.",
            checked_at=_now_iso(),
        )

    headers = api_headers(endpoint)
    headers.pop("Content-Type", None)
    headers.setdefault("Accept", "application/json")
    headers.setdefault("User-Agent", "ImperialAutoGovernor/0.6")
    request = urllib.request.Request(
        api_url(endpoint, endpoint.models_path),
        headers=headers,
        method="GET",
    )
    try:
        with opener(request, timeout=endpoint.probe_timeout_seconds) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Models response exceeded the 2 MB safety limit.")
            payload = json.loads(raw.decode("utf-8"))
            records = _model_records(payload)
    except urllib.error.HTTPError as error:
        return _http_failure(endpoint, error)
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ) as error:
        return EndpointProbeResult(
            endpoint_id=endpoint.endpoint_id,
            status="unreachable",
            detail=f"{type(error).__name__}: {error}",
            checked_at=_now_iso(),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        return EndpointProbeResult(
            endpoint_id=endpoint.endpoint_id,
            status="reachable_unconfirmed",
            detail=f"The models route responded, but its payload was unusable: {error}",
            checked_at=_now_iso(),
            http_status=200,
        )

    ids = tuple(
        str(item["id"])
        for item in records
        if item.get("id") is not None
    )
    selected = next(
        (item for item in records if str(item.get("id")) == endpoint.model),
        None,
    )
    found = selected is not None
    return EndpointProbeResult(
        endpoint_id=endpoint.endpoint_id,
        status="available" if found else "reachable_unconfirmed",
        detail=(
            "The configured model is present in the models response."
            if found
            else "The endpoint responded, but the configured model was not listed."
        ),
        checked_at=_now_iso(),
        http_status=200,
        model_found=found,
        discovered_model_count=len(ids),
        discovered_model_ids=ids[:256],
        selected_model_metadata=(
            _selected_metadata(selected) if selected is not None else None
        ),
    )
