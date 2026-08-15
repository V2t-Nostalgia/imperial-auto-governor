"""In-memory runtime configuration with explicit JSON persistence."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from iag.infrastructure.llm.application_model_profile import (
    ApplicationModelProfile,
)
from iag.infrastructure.llm.model_pool import ModelEndpoint, ModelPool


DEFAULT_APPLICATION_ID = "economy_governance"

# These values belong to one Application model profile, not to an API endpoint.
APPLICATION_OPTION_KEYS = {
    "context_compression_enabled",
    "context_compression_trigger_percent",
    "context_compression_target_percent",
    "context_compression_min_recent_segments",
    "model_template_id",
    "tool_calling_enabled",
    "web_research_enabled",
    "searxng_url",
    "crawl4ai_url",
    "crawl4ai_api_token_file",
    "stellaris_wiki_api_url",
    "web_fetch_maximum_chars",
    "web_fetch_direct_fallback_enabled",
    "web_search_allowed_domains",
}

LEGACY_REQUEST_OPTION_KEYS = {
    "reasoning_effort",
    "request_body_overrides",
    "temperature",
    "thinking",
}

LEGACY_ENDPOINT_KEYS = {
    "api_key",
    "api_key_env",
    "api_key_file",
    "api_key_header",
    "api_key_prefix",
    "auth_mode",
    "base_url",
    "chat_completions_path",
    "context_output_reserve_tokens",
    "enabled",
    "endpoint_id",
    "extra_headers",
    "max_output_tokens",
    "model",
    "model_context_window_tokens",
    "model_id",
    "model_transport",
    "models_path",
    "priority",
    "probe_timeout_seconds",
    "provider",
    "rate_limit_cooldown_seconds",
    "responses_path",
    "sdk_max_retries",
    "supports_reasoning",
    "supports_tools",
    "timeout_seconds",
}


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """One isolated view of the model assets selected for an Application."""

    settings: dict[str, Any]
    model_pools: tuple[ModelPool, ...]
    application_model_profiles: tuple[ApplicationModelProfile, ...]
    application_model_bindings: dict[str, str]
    application_id: str
    application_profile: ApplicationModelProfile

    @property
    def model_pool(self) -> ModelPool:
        """Return only endpoints serving the profile's selected logical model."""
        pool = next(
            item
            for item in self.model_pools
            if item.pool_id == self.application_profile.pool_id
        )
        return pool.model_copy(
            update={
                "endpoints": [
                    endpoint.model_copy(deep=True)
                    for endpoint in pool.endpoints
                    if endpoint.model_id == self.application_profile.model_id
                ]
            },
            deep=True,
        )

    @property
    def request_options(self) -> dict[str, Any]:
        return copy.deepcopy(self.application_profile.request_options)

    @property
    def endpoint(self) -> ModelEndpoint:
        """Compatibility view of the highest-priority enabled endpoint."""
        candidates = sorted(
            (
                (index, endpoint)
                for index, endpoint in enumerate(self.model_pool.endpoints)
                if endpoint.enabled
            ),
            key=lambda item: (item[1].priority, item[0]),
        )
        if not candidates:
            raise ValueError(
                "The active Application model has no enabled endpoint."
            )
        return candidates[0][1].model_copy(deep=True)


class RuntimeConfig:
    """Own model catalogs and application bindings; persist only on save()."""

    _MODEL_KEYS = {
        "endpoint",
        "model_pool",
        "model_pool_id",
        "model_pools",
        "request_options",
        "application_model_profiles",
        "application_model_bindings",
    } | LEGACY_ENDPOINT_KEYS | LEGACY_REQUEST_OPTION_KEYS

    def __init__(self, source_path: Path, document: Mapping[str, Any]) -> None:
        self.source_path = source_path.resolve()
        self._lock = RLock()
        self._load_document(document)

    @classmethod
    def load(cls, source_path: Path) -> "RuntimeConfig":
        path = source_path.resolve()
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("Runtime configuration must be a JSON object.")
        return cls(path, value)

    def _legacy_api_key(self, document: Mapping[str, Any]) -> str | None:
        direct = str(document.get("api_key", "")).strip()
        if direct:
            return direct

        environment_name = str(
            document.get("api_key_env", "IAG_LLM_API_KEY")
        ).strip()
        if environment_name:
            environment_value = os.environ.get(environment_name, "").strip()
            if environment_value:
                return environment_value

        raw_path = str(document.get("api_key_file", "")).strip()
        if not raw_path:
            return None
        expanded = Path(os.path.expandvars(raw_path)).expanduser()
        candidates = (
            (expanded,)
            if expanded.is_absolute()
            else (self.source_path.parent / expanded, expanded)
        )
        for candidate in candidates:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                return value or None
        return None

    def _legacy_flat_endpoint(
        self,
        document: Mapping[str, Any],
    ) -> ModelEndpoint:
        model = str(document.get("model", "")).strip()
        base_url = str(document.get("base_url", "")).strip()
        if not model or not base_url:
            raise ValueError(
                "Legacy runtime configuration requires both model and base_url."
            )

        context_window = int(
            document.get("model_context_window_tokens", 128_000)
        )
        output_reserve = int(
            document.get(
                "max_output_tokens",
                document.get("context_output_reserve_tokens", 8_192),
            )
        )
        auth_mode = str(document.get("auth_mode", "bearer"))
        api_key = None if auth_mode == "none" else self._legacy_api_key(document)
        if auth_mode == "bearer" and not api_key:
            raise ValueError(
                "Legacy runtime configuration could not resolve its API key "
                "from api_key, api_key_env, or api_key_file."
            )

        return ModelEndpoint(
            endpoint_id=str(
                document.get("endpoint_id", "legacy-default")
            ).strip()
            or "legacy-default",
            display_name=str(document.get("display_name", model)).strip()
            or model,
            model_id=str(document.get("model_id", model)).strip() or model,
            model=model,
            model_transport=str(document.get("model_transport", "openai_sdk")),
            provider=str(
                document.get("provider", "responses_compatible")
            ),
            base_url=base_url,
            supports_reasoning=bool(
                document.get(
                    "supports_reasoning",
                    document.get("thinking") or document.get("reasoning_effort"),
                )
            ),
            model_context_window_tokens=context_window,
            auth_mode=auth_mode,
            api_key=api_key,
            max_output_tokens=min(max(output_reserve, 1), context_window),
            priority=int(document.get("priority", 0)),
            enabled=bool(document.get("enabled", True)),
            supports_tools=bool(
                document.get(
                    "supports_tools",
                    document.get("tool_calling_enabled", True),
                )
            ),
            chat_completions_path=str(
                document.get("chat_completions_path", "/chat/completions")
            ),
            responses_path=str(document.get("responses_path", "/responses")),
            models_path=document.get("models_path", "/models"),
            timeout_seconds=int(document.get("timeout_seconds", 120)),
            probe_timeout_seconds=int(
                document.get("probe_timeout_seconds", 10)
            ),
            rate_limit_cooldown_seconds=int(
                document.get("rate_limit_cooldown_seconds", 300)
            ),
            sdk_max_retries=int(document.get("sdk_max_retries", 2)),
            extra_headers=dict(document.get("extra_headers", {})),
            api_key_header=str(
                document.get("api_key_header", "Authorization")
            ),
            api_key_prefix=str(document.get("api_key_prefix", "Bearer ")),
        )

    def _legacy_pool(self, document: Mapping[str, Any]) -> ModelPool:
        pool_value = document.get("model_pool")
        if isinstance(pool_value, Mapping):
            return ModelPool.model_validate(pool_value)
        endpoint_value = document.get("endpoint")
        if isinstance(endpoint_value, Mapping):
            endpoint = ModelEndpoint.model_validate(endpoint_value)
        elif "model" in document or "base_url" in document:
            endpoint = self._legacy_flat_endpoint(document)
        else:
            raise ValueError(
                "Runtime configuration requires model_pools, model_pool, "
                "a legacy endpoint object, or legacy root model fields."
            )
        pool_id = str(document.get("model_pool_id", "default")).strip() or "default"
        return ModelPool(
            pool_id=pool_id,
            display_name=pool_id,
            endpoints=[endpoint],
        )

    def _pools_from_document(
        self,
        document: Mapping[str, Any],
    ) -> list[ModelPool]:
        value = document.get("model_pools")
        if value is None:
            return [self._legacy_pool(document)]
        if not isinstance(value, list) or not value:
            raise ValueError("model_pools must be a non-empty JSON array.")
        return [ModelPool.model_validate(item) for item in value]

    @staticmethod
    def _default_model_id(pool: ModelPool) -> str:
        candidates = sorted(
            enumerate(pool.endpoints),
            key=lambda item: (not item[1].enabled, item[1].priority, item[0]),
        )
        if not candidates:
            raise ValueError("The initial model pool contains no endpoint.")
        return candidates[0][1].model_id

    @classmethod
    def _profiles_from_document(
        cls,
        document: Mapping[str, Any],
        pools: Sequence[ModelPool],
    ) -> list[ApplicationModelProfile]:
        value = document.get("application_model_profiles")
        if value is not None:
            if not isinstance(value, list) or not value:
                raise ValueError(
                    "application_model_profiles must be a non-empty JSON array."
                )
            return [ApplicationModelProfile.model_validate(item) for item in value]

        request_options = document.get("request_options", {})
        if not isinstance(request_options, Mapping):
            raise ValueError("request_options must be a JSON object.")
        request_options = copy.deepcopy(dict(request_options))
        for key in LEGACY_REQUEST_OPTION_KEYS:
            if key in document and key not in request_options:
                request_options[key] = copy.deepcopy(document[key])
        application_options = {
            key: copy.deepcopy(document[key])
            for key in APPLICATION_OPTION_KEYS
            if key in document
        }
        initial_pool = pools[0]
        return [
            ApplicationModelProfile(
                profile_id="economy-default",
                display_name="经济治理默认配置",
                application_id=DEFAULT_APPLICATION_ID,
                pool_id=initial_pool.pool_id,
                model_id=cls._default_model_id(initial_pool),
                request_options=request_options,
                application_options=application_options,
            )
        ]

    @staticmethod
    def _bindings_from_document(
        document: Mapping[str, Any],
        profiles: Sequence[ApplicationModelProfile],
    ) -> dict[str, str]:
        raw = document.get("application_model_bindings")
        if raw is not None and not isinstance(raw, Mapping):
            raise ValueError("application_model_bindings must be a JSON object.")
        bindings = {
            str(key): str(value)
            for key, value in dict(raw or {}).items()
        }
        for profile in profiles:
            bindings.setdefault(profile.application_id, profile.profile_id)
        return bindings

    @staticmethod
    def _validate_catalog(
        pools: Sequence[ModelPool],
        profiles: Sequence[ApplicationModelProfile],
        bindings: Mapping[str, str],
    ) -> tuple[
        list[ModelPool],
        list[ApplicationModelProfile],
        dict[str, str],
    ]:
        validated_pools = [
            ModelPool.model_validate(item.model_dump(mode="python")).model_copy(
                deep=True
            )
            for item in pools
        ]
        if not validated_pools:
            raise ValueError("At least one model pool is required.")
        pool_ids = [item.pool_id for item in validated_pools]
        if len(pool_ids) != len(set(pool_ids)):
            raise ValueError("pool_id must be unique.")

        validated_profiles = [
            ApplicationModelProfile.model_validate(
                item.model_dump(mode="python")
            ).model_copy(deep=True)
            for item in profiles
        ]
        if not validated_profiles:
            raise ValueError("At least one Application model profile is required.")
        profile_ids = [item.profile_id for item in validated_profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("profile_id must be unique.")

        pools_by_id = {item.pool_id: item for item in validated_pools}
        profiles_by_id = {item.profile_id: item for item in validated_profiles}
        for profile in validated_profiles:
            pool = pools_by_id.get(profile.pool_id)
            if pool is None:
                raise ValueError(
                    f"Profile {profile.profile_id!r} references an unknown pool."
                )
            if profile.model_id not in pool.model_ids():
                raise ValueError(
                    f"Profile {profile.profile_id!r} references an unknown model."
                )

        validated_bindings = {
            str(key): str(value) for key, value in bindings.items()
        }
        for application_id, profile_id in validated_bindings.items():
            profile = profiles_by_id.get(profile_id)
            if profile is None or profile.application_id != application_id:
                raise ValueError(
                    f"Application {application_id!r} has an invalid profile binding."
                )
            pool = pools_by_id[profile.pool_id]
            if not any(
                endpoint.enabled and endpoint.model_id == profile.model_id
                for endpoint in pool.endpoints
            ):
                raise ValueError(
                    f"The active profile for {application_id!r} has no enabled endpoint."
                )
        if DEFAULT_APPLICATION_ID not in validated_bindings:
            raise ValueError(
                f"A binding for {DEFAULT_APPLICATION_ID!r} is required."
            )
        return validated_pools, validated_profiles, validated_bindings

    def _load_document(self, document: Mapping[str, Any]) -> None:
        pools = self._pools_from_document(document)
        profiles = self._profiles_from_document(document, pools)
        bindings = self._bindings_from_document(document, profiles)
        pools, profiles, bindings = self._validate_catalog(
            pools,
            profiles,
            bindings,
        )
        self._model_pools = pools
        self._application_model_profiles = profiles
        self._application_model_bindings = bindings
        self._settings = copy.deepcopy(
            {
                key: value
                for key, value in document.items()
                if key not in self._MODEL_KEYS
                and key not in APPLICATION_OPTION_KEYS
            }
        )

    def _snapshot_unlocked(self, application_id: str) -> RuntimeSnapshot:
        profile_id = self._application_model_bindings.get(application_id)
        profile = next(
            (
                item
                for item in self._application_model_profiles
                if item.profile_id == profile_id
            ),
            None,
        )
        if profile is None:
            raise KeyError(
                f"Application {application_id!r} has no active model profile."
            )
        settings = copy.deepcopy(self._settings)
        settings.update(copy.deepcopy(profile.application_options))
        return RuntimeSnapshot(
            settings=settings,
            model_pools=tuple(
                item.model_copy(deep=True) for item in self._model_pools
            ),
            application_model_profiles=tuple(
                item.model_copy(deep=True)
                for item in self._application_model_profiles
            ),
            application_model_bindings=copy.deepcopy(
                self._application_model_bindings
            ),
            application_id=application_id,
            application_profile=profile.model_copy(deep=True),
        )

    def snapshot(
        self,
        application_id: str = DEFAULT_APPLICATION_ID,
    ) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot_unlocked(application_id)

    def update(
        self,
        *,
        model_pools: Sequence[ModelPool] | None = None,
        application_model_profiles: Sequence[ApplicationModelProfile] | None = None,
        application_model_bindings: Mapping[str, str] | None = None,
        model_pool: ModelPool | None = None,
        endpoint: ModelEndpoint | None = None,
        request_options: Mapping[str, Any] | None = None,
        settings: Mapping[str, Any] | None = None,
        application_id: str = DEFAULT_APPLICATION_ID,
    ) -> RuntimeSnapshot:
        """Atomically replace supplied catalogs or compatibility sections."""
        if model_pools is not None and model_pool is not None:
            raise ValueError("Supply model_pools or model_pool, not both.")
        if model_pool is not None and endpoint is not None:
            raise ValueError("Supply model_pool or endpoint, not both.")
        setting_changes = dict(settings or {})
        if self._MODEL_KEYS.intersection(setting_changes):
            raise ValueError("Model settings must use their dedicated arguments.")

        with self._lock:
            pools = [item.model_copy(deep=True) for item in self._model_pools]
            profiles = [
                item.model_copy(deep=True)
                for item in self._application_model_profiles
            ]
            bindings = copy.deepcopy(self._application_model_bindings)

            if model_pools is not None:
                pools = [item.model_copy(deep=True) for item in model_pools]
            elif model_pool is not None:
                pools = [
                    model_pool.model_copy(deep=True)
                    if item.pool_id == model_pool.pool_id
                    else item
                    for item in pools
                ]
                if not any(item.pool_id == model_pool.pool_id for item in pools):
                    pools.append(model_pool.model_copy(deep=True))
            elif endpoint is not None:
                active = self._snapshot_unlocked(application_id)
                pools = [
                    pool.model_copy(
                        update={
                            "endpoints": [
                                endpoint.model_copy(deep=True)
                                if item.endpoint_id == endpoint.endpoint_id
                                else item
                                for item in pool.endpoints
                            ]
                        },
                        deep=True,
                    )
                    if pool.pool_id == active.application_profile.pool_id
                    else pool
                    for pool in pools
                ]

            if application_model_profiles is not None:
                profiles = [
                    item.model_copy(deep=True)
                    for item in application_model_profiles
                ]
            if application_model_bindings is not None:
                bindings = {
                    str(key): str(value)
                    for key, value in application_model_bindings.items()
                }

            active_profile_id = bindings.get(application_id)
            profile_index = next(
                (
                    index
                    for index, item in enumerate(profiles)
                    if item.profile_id == active_profile_id
                ),
                None,
            )
            if profile_index is None:
                raise ValueError(
                    f"Application {application_id!r} has no editable active profile."
                )
            active_profile = profiles[profile_index]
            if model_pool is not None:
                active_profile = active_profile.model_copy(
                    update={
                        "pool_id": model_pool.pool_id,
                        "model_id": self._default_model_id(model_pool),
                    },
                    deep=True,
                )
            if request_options is not None:
                active_profile = active_profile.model_copy(
                    update={"request_options": dict(request_options)},
                    deep=True,
                )

            application_changes = {
                key: copy.deepcopy(value)
                for key, value in setting_changes.items()
                if key in APPLICATION_OPTION_KEYS
            }
            base_changes = {
                key: copy.deepcopy(value)
                for key, value in setting_changes.items()
                if key not in APPLICATION_OPTION_KEYS
            }
            if application_changes:
                options = copy.deepcopy(active_profile.application_options)
                options.update(application_changes)
                active_profile = active_profile.model_copy(
                    update={"application_options": options},
                    deep=True,
                )
            profiles[profile_index] = active_profile

            pools, profiles, bindings = self._validate_catalog(
                pools,
                profiles,
                bindings,
            )
            next_settings = copy.deepcopy(self._settings)
            next_settings.update(base_changes)
            json.dumps(next_settings, ensure_ascii=False)
            self._model_pools = pools
            self._application_model_profiles = profiles
            self._application_model_bindings = bindings
            self._settings = next_settings
            return self._snapshot_unlocked(application_id)

    def reload(self) -> RuntimeSnapshot:
        value = json.loads(self.source_path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("Runtime configuration must be a JSON object.")
        with self._lock:
            self._load_document(value)
            return self._snapshot_unlocked(DEFAULT_APPLICATION_ID)

    @staticmethod
    def _public_pool_document(pool: ModelPool) -> dict[str, Any]:
        endpoints: list[dict[str, Any]] = []
        for endpoint in pool.endpoints:
            item = endpoint.model_dump(mode="json", exclude={"api_key"})
            if endpoint.api_key is not None:
                item["api_key"] = endpoint.api_key.get_secret_value()
            endpoints.append(item)
        return {
            "pool_id": pool.pool_id,
            "display_name": pool.display_name,
            "endpoints": endpoints,
        }

    def document(self) -> dict[str, Any]:
        with self._lock:
            return {
                **copy.deepcopy(self._settings),
                "model_pools": [
                    self._public_pool_document(pool)
                    for pool in self._model_pools
                ],
                "application_model_profiles": [
                    profile.model_dump(mode="json")
                    for profile in self._application_model_profiles
                ],
                "application_model_bindings": copy.deepcopy(
                    self._application_model_bindings
                ),
            }

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(
                self.document(),
                ensure_ascii=False,
                indent=2,
            ) + "\n"
            self.source_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.source_path.with_suffix(
                self.source_path.suffix + ".tmp"
            )
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.source_path)
