"""Semantic execution broker and backend contract.

The broker owns validation, permission checks, stale-snapshot guards,
process-local idempotency, side-effect serialization and result normalization.
Backends own transport/runtime resolution and machine evidence.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import (
    ConfigDict,
    Field,
    SerializeAsAny,
    create_model,
    field_validator,
    model_validator,
)

from iag.core.contracts import (
    ExecutionResult,
    ExecutionStatus,
    FrozenContract,
    MessageKind,
)
from iag.stellaris.execution.action_registry import (
    ActionRegistry,
    ActionRisk,
    ActionSpec,
    BackendId,
    BackendVerification,
    ResearchTarget,
    SemanticTarget,
    VerificationState,
    builtin_action_registry,
)
from iag.stellaris.execution.native_runtime import (
    NativeRuntimeClient,
    NativeRuntimeError,
    NativeRuntimeManifest,
    NativeRuntimeOutcome,
    NativeRuntimeParameter,
    NativeRuntimeTool,
)
from iag.stellaris.execution.session_proxy_controller import (
    SessionProxyController,
    SessionProxyError,
)

_SIDE_EFFECT_LOCK = threading.RLock()
_IDEMPOTENCY_LIMIT = 2048
# TODO(native-runtime): move request fingerprints and terminal/indeterminate
# results into the campaign ledger before claiming restart-safe exactly-once.
_COMPLETED_REQUESTS: OrderedDict[str, tuple[str, ExecutionResult]] = OrderedDict()
_COMPLETED_SEQUENCES: OrderedDict[str, tuple[str, ExecutionSequenceResult]] = (
    OrderedDict()
)


class ExecutionBrokerError(RuntimeError):
    """A public execution contract or dispatch invariant was violated."""


class IdempotencyConflict(ExecutionBrokerError):
    """A request id was reused for a different semantic action."""


class SourceSnapshot(FrozenContract):
    """Stable identity of the save used to validate an action."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    campaign_id: str = Field(min_length=1, max_length=96)
    revision: int = Field(ge=0)
    save_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    game_date: str | None = Field(default=None, max_length=40)


class ExecutionAuthority(FrozenContract):
    """Player-derived authority facts; never populated from model text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    application_id: str = Field(min_length=1, max_length=96)
    execution_authorized: bool
    granted_action_types: tuple[str, ...]
    allowed_risk_classes: tuple[ActionRisk, ...]
    actor_country_id: int | None = Field(default=None, ge=0)
    permission_revision: int = Field(default=0, ge=0)


class CandidateIdentity(FrozenContract):
    """Binds an Application candidate to the exact semantic target selected."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    candidate_id: str = Field(min_length=1, max_length=200)
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def for_target(
        cls,
        candidate_id: str,
        target: SemanticTarget,
    ) -> CandidateIdentity:
        return cls(
            candidate_id=candidate_id,
            target_sha256=semantic_target_fingerprint(target),
        )


def semantic_target_fingerprint(target: SemanticTarget) -> str:
    """Return a stable identity without exposing a backend representation."""

    encoded = json.dumps(
        target.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PreparedAction(FrozenContract):
    """A candidate-backed semantic action ready for deterministic dispatch."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        arbitrary_types_allowed=True,
    )

    schema_version: Literal["iag.prepared_action.v1"] = "iag.prepared_action.v1"
    request_id: str = Field(min_length=1, max_length=96)
    action_intent_id: str = Field(min_length=1, max_length=200)
    application_id: str = Field(min_length=1, max_length=96)
    action_type: str = Field(min_length=1, max_length=64)
    action_version: int = Field(default=1, ge=1)
    target: SerializeAsAny[SemanticTarget]
    candidate: CandidateIdentity
    source_snapshot: SourceSnapshot
    authority: ExecutionAuthority

    @field_validator("target", mode="before")
    @classmethod
    def require_typed_target(cls, value: object) -> object:
        if not isinstance(value, SemanticTarget):
            raise TypeError("PreparedAction.target must be a SemanticTarget instance.")
        return value

    @model_validator(mode="after")
    def validate_bindings(self) -> PreparedAction:
        if self.authority.application_id != self.application_id:
            raise ValueError("Authority belongs to a different Application.")
        if self.candidate.target_sha256 != semantic_target_fingerprint(self.target):
            raise ValueError("Candidate identity does not match the semantic target.")
        return self


class OrderedActionSequence(FrozenContract):
    """Ordered, lock-held actions; this does not promise rollback or atomicity."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        arbitrary_types_allowed=True,
    )

    schema_version: Literal["iag.ordered_action_sequence.v1"] = (
        "iag.ordered_action_sequence.v1"
    )
    request_id: str = Field(min_length=1, max_length=96)
    actions: tuple[PreparedAction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_shared_context(self) -> OrderedActionSequence:
        first = self.actions[0]
        request_ids = {action.request_id for action in self.actions}
        if len(request_ids) != len(self.actions):
            raise ValueError("Ordered sequence step request ids must be unique.")
        for action in self.actions[1:]:
            if action.application_id != first.application_id:
                raise ValueError("Ordered sequence cannot cross Applications.")
            if action.source_snapshot != first.source_snapshot:
                raise ValueError("Ordered sequence must use one source snapshot.")
            if action.authority != first.authority:
                raise ValueError("Ordered sequence must use one authority context.")
        return self


class BackendExecutionResult(FrozenContract):
    """Normalized evidence returned by one execution backend."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    backend_id: BackendId
    status: ExecutionStatus
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class BackendSequenceResult(FrozenContract):
    """Attempted prefix of an ordered backend sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    backend_id: BackendId
    requested_steps: int = Field(ge=1)
    results: tuple[BackendExecutionResult, ...]
    error: str | None = None


class ExecutionSequenceResult(FrozenContract):
    """Public result for a non-transactional ordered sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["iag.execution_sequence_result.v1"] = (
        "iag.execution_sequence_result.v1"
    )
    request_id: str
    status: ExecutionStatus
    requested_steps: int = Field(ge=1)
    attempted_steps: int = Field(ge=0)
    results: tuple[ExecutionResult, ...]
    error: str | None = None


class ExecutionToolParameter(FrozenContract):
    """Backend-neutral parameter metadata visible to an Application."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    parameter_type: Literal["uint32", "string", "boolean"]
    required: bool
    description: str
    minimum: int | None = None
    maximum: int | None = None
    maximum_length: int | None = None


class ExecutionToolDescriptor(FrozenContract):
    """A discovered semantic tool and its default Application assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_type: str
    action_version: int = Field(ge=1)
    semantic_capability: str
    application_id: str
    description: str
    risk_class: ActionRisk
    verification_state: VerificationState
    backend_id: BackendId
    parameters: tuple[ExecutionToolParameter, ...]
    execution_ready: bool


class BrokerCapabilities(FrozenContract):
    """Semantic capabilities currently dispatchable through registered backends."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["iag.execution_capabilities.v1"] = (
        "iag.execution_capabilities.v1"
    )
    capabilities: tuple[str, ...]
    backend_ids: tuple[BackendId, ...]
    tools: tuple[ExecutionToolDescriptor, ...] = ()


@runtime_checkable
class ExecutionBackend(Protocol):
    """Contract implemented by session proxy, carrier click and native runtime."""

    backend_id: BackendId

    def capabilities(self) -> tuple[str, ...]: ...

    def supports(self, spec: ActionSpec) -> bool: ...

    def execute(self, action: PreparedAction) -> BackendExecutionResult: ...

    def execute_ordered_sequence(
        self,
        sequence: OrderedActionSequence,
    ) -> BackendSequenceResult: ...


SnapshotValidator = Callable[[SourceSnapshot], None]
CandidateValidator = Callable[[PreparedAction], None]
AuditSink = Callable[[ExecutionResult], None]


def _proxy_status(raw: Mapping[str, Any]) -> ExecutionStatus:
    outcome = str(raw.get("outcome") or "").strip().lower()
    if outcome == "confirmed":
        return "confirmed_by_packet"
    if outcome in {"rejected", "denied"}:
        return "rejected"
    if outcome == "cancelled":
        return "cancelled"
    return "failed"


class SessionProxyBackend:
    """Adapter around the existing verified SessionProxyController."""

    backend_id = BackendId.SESSION_PROXY

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        controller_factory: Callable[[dict[str, Any]], SessionProxyController] = (
            SessionProxyController
        ),
    ) -> None:
        self._config = dict(config)
        self._controller_factory = controller_factory

    def capabilities(self) -> tuple[str, ...]:
        return (
            "stellaris.execution.v1",
            "stellaris.action.start_research.v1",
            "stellaris.action.stop_research.v1",
        )

    def supports(self, spec: ActionSpec) -> bool:
        return (
            spec.action_type in {"start_research", "stop_research"}
            and self.backend_id in spec.broker_enabled_backends
        )

    @staticmethod
    def _target(action: PreparedAction) -> dict[str, Any]:
        if action.action_type not in {"start_research", "stop_research"}:
            raise ExecutionBrokerError(
                f"SessionProxyBackend has no semantic resolver for "
                f"{action.action_type!r}."
            )
        target = action.target
        if not isinstance(target, ResearchTarget):
            raise ExecutionBrokerError("Research action has a non-research target.")
        actor_country_id = action.authority.actor_country_id
        if actor_country_id is None:
            raise ExecutionBrokerError(
                "Research proxy resolution requires an authority country id."
            )
        return {
            "context_822c": actor_country_id,
            "technology_id": target.technology_id,
        }

    def _normalize(self, raw: Mapping[str, Any]) -> BackendExecutionResult:
        error = str(raw.get("error") or "").strip() or None
        return BackendExecutionResult(
            backend_id=self.backend_id,
            status=_proxy_status(raw),
            evidence={"raw_session_proxy_result": dict(raw)},
            error=error,
        )

    def execute(self, action: PreparedAction) -> BackendExecutionResult:
        controller = self._controller_factory(dict(self._config))
        try:
            raw = controller.arm_and_wait(
                action=action.action_type,
                target=self._target(action),
                request_id=action.request_id,
            )
        except SessionProxyError as error:
            return BackendExecutionResult(
                backend_id=self.backend_id,
                status="failed",
                evidence={},
                error=str(error),
            )
        return self._normalize(raw)

    def execute_ordered_sequence(
        self,
        sequence: OrderedActionSequence,
    ) -> BackendSequenceResult:
        controller = self._controller_factory(dict(self._config))
        steps = [
            {"action": action.action_type, "target": self._target(action)}
            for action in sequence.actions
        ]
        try:
            raw = controller.arm_sequence_and_wait(
                steps=steps,
                request_id=sequence.request_id,
            )
        except SessionProxyError as error:
            return BackendSequenceResult(
                backend_id=self.backend_id,
                requested_steps=len(sequence.actions),
                results=(),
                error=str(error),
            )
        raw_results = raw.get("results")
        if not isinstance(raw_results, list):
            raw_results = []
        results = tuple(
            self._normalize(item) for item in raw_results if isinstance(item, Mapping)
        )
        error = None
        if not results and str(raw.get("outcome") or "") != "confirmed":
            error = str(raw.get("error") or "Session proxy sequence failed.")
        return BackendSequenceResult(
            backend_id=self.backend_id,
            requested_steps=len(sequence.actions),
            results=results,
            error=error,
        )


class NativeRuntimeBackend:
    """Adapter for self-described commands in a platform-native game runtime."""

    backend_id = BackendId.NATIVE_RUNTIME

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        client_factory: Callable[..., NativeRuntimeClient] = NativeRuntimeClient,
    ) -> None:
        self._enabled = bool(config.get("native_runtime_enabled", False))
        endpoint = config.get("native_runtime_endpoint") or config.get(
            "native_runtime_socket_path"
        )
        timeout = float(config.get("native_runtime_timeout_seconds") or 20.0)
        self._client = client_factory(
            endpoint,
            timeout_seconds=timeout,
            loader_path=config.get("native_runtime_loader_path"),
            library_path=config.get("native_runtime_library_path"),
        )

    def manifest(self, *, refresh: bool = False) -> NativeRuntimeManifest:
        if not self._enabled or not self._client.ensure_available():
            raise NativeRuntimeError("Native runtime is unavailable.")
        return self._client.describe_tools(refresh=refresh)

    def capabilities(self) -> tuple[str, ...]:
        base = {
            "stellaris.execution.v1",
            "stellaris.execution.native_runtime.v1",
        }
        try:
            base.update(tool.semantic_capability for tool in self.manifest().tools)
        except NativeRuntimeError:
            pass
        return tuple(sorted(base))

    @staticmethod
    def _parameter_compatible(
        parameter: NativeRuntimeParameter,
        spec: ActionSpec,
    ) -> bool:
        field = spec.target_model.model_fields.get(parameter.name)
        if field is None:
            return False
        expected = {
            "uint32": int,
            "string": str,
            "boolean": bool,
        }[parameter.parameter_type]
        return field.annotation is expected

    def _tool_for(self, spec: ActionSpec) -> NativeRuntimeTool | None:
        try:
            tool = self.manifest().get(spec.action_type, spec.action_version)
        except (KeyError, NativeRuntimeError):
            return None
        if tool.risk_class != spec.risk_class.value:
            return None
        if any(
            not self._parameter_compatible(parameter, spec)
            for parameter in tool.parameters
        ):
            return None
        return tool

    def supports(self, spec: ActionSpec) -> bool:
        return (
            self._enabled
            and self.backend_id in spec.broker_enabled_backends
            and self._client.ensure_available()
            and self._tool_for(spec) is not None
        )

    def tool_descriptors(
        self,
        registry: ActionRegistry,
    ) -> tuple[ExecutionToolDescriptor, ...]:
        try:
            tools = self.manifest().tools
        except NativeRuntimeError:
            return ()
        result: list[ExecutionToolDescriptor] = []
        for tool in tools:
            try:
                spec = registry.get(tool.action_type, tool.action_version)
                ready = self.supports(spec)
            except KeyError:
                ready = False
            result.append(
                ExecutionToolDescriptor(
                    action_type=tool.action_type,
                    action_version=tool.action_version,
                    semantic_capability=tool.semantic_capability,
                    application_id=tool.application_id or "etc",
                    description=tool.description,
                    risk_class=ActionRisk(tool.risk_class),
                    verification_state=VerificationState(
                        tool.verification_state
                    ),
                    backend_id=self.backend_id,
                    parameters=tuple(
                        ExecutionToolParameter(
                            name=parameter.name,
                            parameter_type=parameter.parameter_type,
                            required=parameter.required,
                            description=parameter.description,
                            minimum=parameter.minimum,
                            maximum=parameter.maximum,
                            maximum_length=parameter.maximum_length,
                        )
                        for parameter in tool.parameters
                    ),
                    execution_ready=ready,
                )
            )
        return tuple(result)

    @staticmethod
    def _normalize(response: Any) -> BackendExecutionResult:
        if response.outcome == NativeRuntimeOutcome.CONFIRMED:
            status: ExecutionStatus = "confirmed_by_packet"
            error = None
        elif response.outcome == NativeRuntimeOutcome.PENDING:
            status = "provisional_pending_save"
            error = response.detail
        elif response.outcome in {
            NativeRuntimeOutcome.REJECTED,
            NativeRuntimeOutcome.CONFLICT,
        }:
            status = "rejected"
            error = response.detail
        else:
            status = "failed"
            error = response.detail
        return BackendExecutionResult(
            backend_id=BackendId.NATIVE_RUNTIME,
            status=status,
            evidence={
                "native_runtime_result": response.model_dump(mode="json"),
                "confirmation_kind": "native_runtime_postcondition",
            },
            error=error,
        )

    def execute(self, action: PreparedAction) -> BackendExecutionResult:
        country_id = action.authority.actor_country_id
        if country_id is None:
            raise ExecutionBrokerError(
                "Native execution requires an authority country id."
            )
        try:
            response = self._client.execute(
                request_id=action.request_id,
                action_type=action.action_type,
                action_version=action.action_version,
                country_id=country_id,
                target=action.target.model_dump(mode="python"),
            )
        except NativeRuntimeError as error:
            return BackendExecutionResult(
                backend_id=self.backend_id,
                status="failed",
                evidence={},
                error=str(error),
            )
        return self._normalize(response)

    def execute_ordered_sequence(
        self,
        sequence: OrderedActionSequence,
    ) -> BackendSequenceResult:
        if len(sequence.actions) != 1:
            return BackendSequenceResult(
                backend_id=self.backend_id,
                requested_steps=len(sequence.actions),
                results=(),
                error=(
                    "Native runtime actions do not claim multi-step sequence support."
                ),
            )
        return BackendSequenceResult(
            backend_id=self.backend_id,
            requested_steps=1,
            results=(self.execute(sequence.actions[0]),),
        )


class ExecutionBroker:
    """Validate and serialize semantic actions across pluggable backends."""

    def __init__(
        self,
        *,
        registry: ActionRegistry | None = None,
        backends: tuple[ExecutionBackend, ...],
        application_action_types: Mapping[str, frozenset[str]],
        snapshot_validator: SnapshotValidator,
        candidate_validator: CandidateValidator,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.registry = registry or builtin_action_registry()
        self.backends = backends
        self.application_action_types = {
            key: frozenset(value) for key, value in application_action_types.items()
        }
        self.snapshot_validator = snapshot_validator
        self.candidate_validator = candidate_validator
        self.audit_sink = audit_sink

    def capabilities(self) -> BrokerCapabilities:
        capabilities = {"stellaris.execution.v1"}
        backend_ids: set[BackendId] = set()
        tools: dict[
            tuple[str, int, BackendId], ExecutionToolDescriptor
        ] = {}
        for spec in self.registry.all():
            for backend in self.backends:
                if (
                    backend.backend_id in spec.broker_enabled_backends
                    and backend.supports(spec)
                ):
                    capabilities.add(spec.semantic_capability)
                    capabilities.update(backend.capabilities())
                    backend_ids.add(backend.backend_id)
        for backend in self.backends:
            if isinstance(backend, NativeRuntimeBackend):
                for tool in backend.tool_descriptors(self.registry):
                    key = (
                        tool.action_type,
                        tool.action_version,
                        tool.backend_id,
                    )
                    tools[key] = tool
        return BrokerCapabilities(
            capabilities=tuple(sorted(capabilities)),
            backend_ids=tuple(sorted(backend_ids, key=str)),
            tools=tuple(tools[key] for key in sorted(tools, key=str)),
        )

    @staticmethod
    def _fingerprint(action: PreparedAction) -> str:
        encoded = json.dumps(
            action.model_dump(mode="json", exclude_none=False),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _sequence_fingerprint(sequence: OrderedActionSequence) -> str:
        encoded = json.dumps(
            sequence.model_dump(mode="json", exclude_none=False),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _result(
        self,
        action: PreparedAction,
        status: ExecutionStatus,
        *,
        evidence: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> ExecutionResult:
        detail = dict(evidence or {})
        detail.update(
            {
                "action_type": action.action_type,
                "action_version": action.action_version,
                "candidate_id": action.candidate.candidate_id,
            }
        )
        if error:
            detail["error"] = error
        result = ExecutionResult(
            message_id=f"execution:{action.request_id}",
            kind=MessageKind.EXECUTION_RESULT,
            campaign_id=action.source_snapshot.campaign_id,
            application_id=action.application_id,
            created_at=datetime.now(UTC),
            source_revision=action.source_snapshot.revision,
            action_intent_id=action.action_intent_id,
            status=status,
            run_id=action.request_id,
            evidence=detail,
            summary_zh=error or "",
        )
        if self.audit_sink is not None:
            try:
                self.audit_sink(result)
            except Exception as audit_error:  # noqa: BLE001 - preserve execution truth
                result = result.model_copy(
                    update={
                        "evidence": {
                            **result.evidence,
                            "audit_sink_error": str(audit_error),
                        }
                    }
                )
        return result

    def _validate_action(self, action: PreparedAction) -> ActionSpec:
        try:
            spec = self.registry.get(action.action_type, action.action_version)
        except KeyError as error:
            raise ExecutionBrokerError(str(error)) from error
        try:
            spec.validate_target(action.target)
        except (TypeError, ValueError) as error:
            raise ExecutionBrokerError(
                f"Invalid target for {action.action_type}.v{action.action_version}: "
                f"{error}"
            ) from error
        allowed = self.application_action_types.get(action.application_id, frozenset())
        if action.action_type not in allowed:
            raise ExecutionBrokerError(
                f"Application {action.application_id!r} may not use "
                f"{action.action_type!r}."
            )
        if not action.authority.execution_authorized:
            raise ExecutionBrokerError("Player authority does not permit execution.")
        if action.action_type not in action.authority.granted_action_types:
            raise ExecutionBrokerError("Action is outside the current authority grant.")
        if spec.risk_class not in action.authority.allowed_risk_classes:
            raise ExecutionBrokerError(
                f"Authority does not permit risk class {spec.risk_class.value!r}."
            )
        self.snapshot_validator(action.source_snapshot)
        self.candidate_validator(action)
        return spec

    def _select_backend(
        self, specifications: tuple[ActionSpec, ...]
    ) -> ExecutionBackend:
        backend = next(
            (
                backend
                for backend in self.backends
                if all(
                    backend.backend_id in spec.broker_enabled_backends
                    and backend.supports(spec)
                    for spec in specifications
                )
            ),
            None,
        )
        if backend is None:
            capabilities = ", ".join(
                spec.semantic_capability for spec in specifications
            )
            raise ExecutionBrokerError(
                f"No available backend supports the requested action set: "
                f"{capabilities}."
            )
        return backend

    def _validate(self, action: PreparedAction) -> tuple[ActionSpec, ExecutionBackend]:
        spec = self._validate_action(action)
        return spec, self._select_backend((spec,))

    def execute(self, action: PreparedAction) -> ExecutionResult:
        """Execute once; repeating an identical request id returns cached truth."""

        fingerprint = self._fingerprint(action)
        with _SIDE_EFFECT_LOCK:
            if action.request_id in _COMPLETED_SEQUENCES:
                raise IdempotencyConflict(
                    f"request_id {action.request_id!r} was already used by an "
                    "ordered sequence."
                )
            cached = _COMPLETED_REQUESTS.get(action.request_id)
            if cached is not None:
                if cached[0] != fingerprint:
                    raise IdempotencyConflict(
                        f"request_id {action.request_id!r} was reused with a "
                        "different semantic payload."
                    )
                _COMPLETED_REQUESTS.move_to_end(action.request_id)
                return cached[1]
            try:
                _specification, backend = self._validate(action)
            except Exception as error:  # noqa: BLE001 - rejected pre-dispatch
                result = self._result(action, "rejected", error=str(error))
            else:
                try:
                    backend_result = backend.execute(action)
                except Exception as error:  # noqa: BLE001 - uncertain backend failure
                    result = self._result(action, "failed", error=str(error))
                else:
                    result = self._result(
                        action,
                        backend_result.status,
                        evidence={
                            "backend_id": backend_result.backend_id.value,
                            "backend_evidence": backend_result.evidence,
                        },
                        error=backend_result.error,
                    )
            _COMPLETED_REQUESTS[action.request_id] = (fingerprint, result)
            _COMPLETED_REQUESTS.move_to_end(action.request_id)
            while len(_COMPLETED_REQUESTS) > _IDEMPOTENCY_LIMIT:
                _COMPLETED_REQUESTS.popitem(last=False)
            return result

    def execute_sequence(
        self,
        sequence: OrderedActionSequence,
    ) -> ExecutionSequenceResult:
        """Execute an ordered prefix under one lock, without rollback semantics."""

        fingerprint = self._sequence_fingerprint(sequence)
        with _SIDE_EFFECT_LOCK:
            if sequence.request_id in _COMPLETED_REQUESTS:
                raise IdempotencyConflict(
                    f"sequence request_id {sequence.request_id!r} was already "
                    "used by a single action."
                )
            cached = _COMPLETED_SEQUENCES.get(sequence.request_id)
            if cached is not None:
                if cached[0] != fingerprint:
                    raise IdempotencyConflict(
                        f"sequence request_id {sequence.request_id!r} was reused "
                        "with a different semantic payload."
                    )
                _COMPLETED_SEQUENCES.move_to_end(sequence.request_id)
                return cached[1]
            for action in sequence.actions:
                if (
                    action.request_id in _COMPLETED_REQUESTS
                    or action.request_id in _COMPLETED_SEQUENCES
                ):
                    raise IdempotencyConflict(
                        f"sequence step request_id {action.request_id!r} was "
                        "already completed outside this sequence."
                    )
            try:
                specifications = tuple(
                    self._validate_action(action) for action in sequence.actions
                )
                backend = self._select_backend(specifications)
            except Exception as caught:  # noqa: BLE001 - rejected pre-dispatch
                public_results = []
                status = "rejected"
                error = str(caught)
            else:
                try:
                    backend_result = backend.execute_ordered_sequence(sequence)
                except Exception as caught:  # noqa: BLE001 - uncertain backend failure
                    public_results = []
                    status = "failed"
                    error = str(caught)
                else:
                    public_results = []
                    for action, item in zip(
                        sequence.actions,
                        backend_result.results,
                        strict=False,
                    ):
                        public = self._result(
                            action,
                            item.status,
                            evidence={
                                "backend_id": item.backend_id.value,
                                "backend_evidence": item.evidence,
                                "ordered_sequence_id": sequence.request_id,
                            },
                            error=item.error,
                        )
                        public_results.append(public)
                        _COMPLETED_REQUESTS[action.request_id] = (
                            self._fingerprint(action),
                            public,
                        )
                    status = _sequence_status(
                        tuple(result.status for result in public_results),
                        len(sequence.actions),
                    )
                    error = next(
                        (
                            str(result.evidence.get("error"))
                            for result in public_results
                            if result.evidence.get("error")
                        ),
                        backend_result.error,
                    )
            result = ExecutionSequenceResult(
                request_id=sequence.request_id,
                status=status,
                requested_steps=len(sequence.actions),
                attempted_steps=len(public_results),
                results=tuple(public_results),
                error=error,
            )
            _COMPLETED_SEQUENCES[sequence.request_id] = (fingerprint, result)
            _COMPLETED_SEQUENCES.move_to_end(sequence.request_id)
            while len(_COMPLETED_SEQUENCES) > _IDEMPOTENCY_LIMIT:
                _COMPLETED_SEQUENCES.popitem(last=False)
            while len(_COMPLETED_REQUESTS) > _IDEMPOTENCY_LIMIT:
                _COMPLETED_REQUESTS.popitem(last=False)
            return result


def _sequence_status(
    statuses: tuple[ExecutionStatus, ...],
    requested_steps: int,
) -> ExecutionStatus:
    if len(statuses) != requested_steps:
        return "failed"
    if all(status == "confirmed_by_save" for status in statuses):
        return "confirmed_by_save"
    if all(
        status in {"confirmed_by_packet", "confirmed_by_save"} for status in statuses
    ):
        return "confirmed_by_packet"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "rejected" for status in statuses):
        return "rejected"
    if any(status == "cancelled" for status in statuses):
        return "cancelled"
    return "provisional_pending_save"


def _runtime_target_model(tool: NativeRuntimeTool) -> type[SemanticTarget]:
    """Create one strict target model from a trusted compiled tool descriptor."""

    fields: dict[str, tuple[Any, Any]] = {}
    python_types: dict[str, type[Any]] = {
        "uint32": int,
        "string": str,
        "boolean": bool,
    }
    for parameter in tool.parameters:
        annotation = python_types[parameter.parameter_type]
        constraints: dict[str, Any] = {"description": parameter.description}
        if parameter.parameter_type == "uint32":
            constraints.update(
                ge=parameter.minimum,
                le=parameter.maximum,
            )
        elif parameter.parameter_type == "string":
            constraints.update(
                min_length=1,
                max_length=parameter.maximum_length,
            )
        if parameter.required:
            fields[parameter.name] = (annotation, Field(..., **constraints))
        else:
            fields[parameter.name] = (
                annotation | None,
                Field(default=None, **constraints),
            )
    class_name = "".join(part.capitalize() for part in tool.action_type.split("_"))
    return create_model(
        f"Runtime{class_name}TargetV{tool.action_version}",
        __base__=SemanticTarget,
        **fields,
    )


def _native_registry_overlay(
    registry: ActionRegistry,
    manifest: NativeRuntimeManifest,
) -> ActionRegistry:
    """Merge a trusted runtime catalog without replacing reviewed contracts."""

    specs: dict[tuple[str, int], ActionSpec] = {
        (item.action_type, item.action_version): item for item in registry.all()
    }
    for tool in manifest.tools:
        key = (tool.action_type, tool.action_version)
        existing = specs.get(key)
        if existing is None:
            state = VerificationState(tool.verification_state)
            specs[key] = ActionSpec(
                action_type=tool.action_type,
                action_version=tool.action_version,
                target_model=_runtime_target_model(tool),
                risk_class=ActionRisk(tool.risk_class),
                semantic_capability=tool.semantic_capability,
                supported_backends=(BackendId.NATIVE_RUNTIME,),
                broker_enabled_backends=(
                    (BackendId.NATIVE_RUNTIME,)
                    if state == VerificationState.LIVE_VERIFIED
                    else ()
                ),
                verification_state=state,
                backend_verification=(
                    BackendVerification(
                        backend_id=BackendId.NATIVE_RUNTIME,
                        verification_state=state,
                    ),
                ),
                migration_note=(
                    "Runtime-discovered semantic contract; defaults to the etc "
                    "Application until its final domain assignment is reviewed."
                ),
            )
            continue
        if existing.risk_class.value != tool.risk_class:
            # Conflicting metadata never weakens the reviewed upper contract.
            continue
        if BackendId.NATIVE_RUNTIME in existing.supported_backends:
            continue
        state = VerificationState(tool.verification_state)
        aggregate = (
            VerificationState.LIVE_VERIFIED
            if existing.verification_state == VerificationState.LIVE_VERIFIED
            or state == VerificationState.LIVE_VERIFIED
            else VerificationState.PAIRED_CAPTURE
        )
        specs[key] = existing.model_copy(
            update={
                "supported_backends": (
                    *existing.supported_backends,
                    BackendId.NATIVE_RUNTIME,
                ),
                "broker_enabled_backends": (
                    *existing.broker_enabled_backends,
                    *((BackendId.NATIVE_RUNTIME,) if state == VerificationState.LIVE_VERIFIED else ()),
                ),
                "verification_state": aggregate,
                "backend_verification": (
                    *existing.backend_verification,
                    BackendVerification(
                        backend_id=BackendId.NATIVE_RUNTIME,
                        verification_state=state,
                    ),
                ),
            }
        )
    return ActionRegistry(tuple(specs[key] for key in sorted(specs)))


def _merge_runtime_assignments(
    registry: ActionRegistry,
    assignments: dict[str, set[str]],
    manifest: NativeRuntimeManifest,
) -> None:
    """Stage new runtime tools without letting a backend rewrite reviewed owners."""

    reviewed = {
        (spec.action_type, spec.action_version) for spec in registry.all()
    }
    for tool in manifest.tools:
        owner = tool.application_id or "etc"
        key = (tool.action_type, tool.action_version)
        if key in reviewed:
            # Existing semantic actions are already assigned by Application
            # manifests. The runtime may describe the same owner, but cannot
            # grant that reviewed action to another Application.
            continue
        assignments.setdefault(owner, set()).add(tool.action_type)


def build_execution_broker(
    config: Mapping[str, Any],
    *,
    application_action_types: Mapping[str, frozenset[str]],
    snapshot_validator: SnapshotValidator,
    candidate_validator: CandidateValidator,
    audit_sink: AuditSink | None = None,
) -> ExecutionBroker:
    """Build the configured platform Broker without exposing backend choice.

    Native runtime and carrier-click composition belongs here (or in a future
    platform composition root), never in an Application.
    """

    backends: list[ExecutionBackend] = []
    registry = builtin_action_registry()
    assignments = {
        key: set(value) for key, value in application_action_types.items()
    }
    assignments.setdefault("etc", set())
    if bool(config.get("native_runtime_enabled", False)):
        native = NativeRuntimeBackend(config)
        backends.append(native)
        try:
            manifest = native.manifest()
        except NativeRuntimeError:
            manifest = None
        if manifest is not None:
            _merge_runtime_assignments(registry, assignments, manifest)
            registry = _native_registry_overlay(registry, manifest)
    backends.append(SessionProxyBackend(config))
    return ExecutionBroker(
        registry=registry,
        backends=tuple(backends),
        application_action_types={
            key: frozenset(value) for key, value in assignments.items()
        },
        snapshot_validator=snapshot_validator,
        candidate_validator=candidate_validator,
        audit_sink=audit_sink,
    )
