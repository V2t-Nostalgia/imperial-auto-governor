from typing import Any, Literal, Mapping

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)

TransportName = Literal["openai_sdk", "anthropic_sdk", "raw_http"]

ProtocolName = Literal[
    "chat_completions_compatible",
    "responses_compatible",
    "anthropic_messages_compatible",
]

TOOL_CALL_PROTOCOLS = frozenset(
    {
        "chat_completions_compatible",
        "anthropic_messages_compatible",
    }
)


class ModelEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint_id: str = Field(min_length=1, max_length=96)
    display_name: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1)
    model_transport: TransportName
    provider: ProtocolName
    base_url: AnyHttpUrl
    supports_reasoning: bool
    model_context_window_tokens: PositiveInt
    auth_mode: Literal["bearer", "none"] = "bearer"
    api_key: SecretStr | None = None
    max_output_tokens: PositiveInt
    priority: int = Field(ge=0)
    enabled: bool
    supports_tools: bool
    chat_completions_path: str = "/chat/completions"
    responses_path: str = "/responses"
    messages_path: str = "/messages"
    models_path: str | None = "/models"
    timeout_seconds: PositiveInt = 120
    probe_timeout_seconds: PositiveInt = 10
    rate_limit_cooldown_seconds: int = Field(default=300, ge=1, le=86_400)
    sdk_max_retries: int = Field(default=2, ge=0, le=10)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "

    @model_validator(mode="before")
    @classmethod
    def supply_legacy_names(cls, value: Any) -> Any:
        """Upgrade pre-catalog endpoints without rewriting their source file."""
        if not isinstance(value, Mapping):
            return value
        upgraded = dict(value)
        endpoint_id = str(upgraded.get("endpoint_id", "")).strip()
        provider_model = str(upgraded.get("model", "")).strip()
        upgraded.setdefault("display_name", endpoint_id or provider_model)
        upgraded.setdefault("model_id", provider_model)
        return upgraded

    @field_validator(
        "chat_completions_path",
        "responses_path",
        "messages_path",
        "models_path",
    )
    @classmethod
    def validate_api_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = value.strip()
        if not path:
            raise ValueError("API paths cannot be blank; use null to disable probing")
        return path if path.startswith("/") else "/" + path

    @model_validator(mode="after")
    def validate_token_limits(self) -> "ModelEndpoint":
        if self.max_output_tokens > self.model_context_window_tokens:
            raise ValueError(
                "max_output_tokens cannot exceed model_context_window_tokens"
            )
        return self

    @model_validator(mode="after")
    def validate_transport_protocol_pair(self) -> "ModelEndpoint":
        if self.model_transport == "anthropic_sdk":
            if self.provider != "anthropic_messages_compatible":
                raise ValueError(
                    "anthropic_sdk requires anthropic_messages_compatible"
                )
            if (
                self.auth_mode != "bearer"
                or self.api_key_header.lower() != "x-api-key"
                or self.api_key_prefix
            ):
                raise ValueError(
                    "anthropic_sdk requires API-key auth with x-api-key and "
                    "an empty prefix; use raw_http for custom authentication"
                )
            if not self.messages_path.endswith("/v1/messages"):
                raise ValueError(
                    "anthropic_sdk requires messages_path ending in "
                    "'/v1/messages'; use raw_http for an arbitrary path"
                )
        elif self.provider == "anthropic_messages_compatible" and (
            self.model_transport == "openai_sdk"
        ):
            raise ValueError(
                "anthropic_messages_compatible cannot use openai_sdk"
            )
        return self

    @model_validator(mode="after")
    def api_verify(self) -> "ModelEndpoint":
        if (
            self.enabled
            and self.auth_mode == "bearer"
            and (
                self.api_key is None
                or not self.api_key.get_secret_value().strip()
            )
        ):
            raise ValueError("An enabled bearer endpoint requires an API key")
        return self


class ModelPool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool_id: str = Field(min_length=1, max_length=96)
    display_name: str = Field(min_length=1, max_length=120)
    endpoints: list[ModelEndpoint] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def supply_legacy_display_name(cls, value: Any) -> Any:
        """Use the old pool identifier as its initial player-visible name."""
        if not isinstance(value, Mapping):
            return value
        upgraded = dict(value)
        upgraded.setdefault("display_name", str(upgraded.get("pool_id", "")))
        return upgraded

    @model_validator(mode="after")
    def validate_unique_endpoint_ids(self) -> "ModelPool":
        endpoint_ids = [endpoint.endpoint_id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("endpoint_id must be unique within a model pool")
        return self

    def model_ids(self, *, enabled_only: bool = False) -> tuple[str, ...]:
        """Return stable logical model identifiers in endpoint display order."""
        values: list[str] = []
        for endpoint in self.endpoints:
            if enabled_only and not endpoint.enabled:
                continue
            if endpoint.model_id not in values:
                values.append(endpoint.model_id)
        return tuple(values)
