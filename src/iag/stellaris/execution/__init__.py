"""Stellaris 副作用执行边界。

固定点击、端口发现、Host Bridge 协议和一次性拦截器都位于此处。Agent 只能
提交经过验证的动作意图，不能绕过这一层直接构造网络包。
"""

from .action_registry import (
    ActionRegistry,
    ActionRisk,
    ActionSpec,
    BackendId,
    BackendVerification,
    ResearchTarget,
    SemanticTarget,
    builtin_action_registry,
)
from .broker import (
    BackendExecutionResult,
    BackendSequenceResult,
    BrokerCapabilities,
    CandidateIdentity,
    ExecutionBackend,
    ExecutionBroker,
    ExecutionBrokerError,
    ExecutionAuthority,
    ExecutionSequenceResult,
    ExecutionToolDescriptor,
    ExecutionToolParameter,
    IdempotencyConflict,
    NativeRuntimeBackend,
    OrderedActionSequence,
    PreparedAction,
    SourceSnapshot,
    build_execution_broker,
)
from .native_runtime import (
    NativeRuntimeClient,
    NativeRuntimeError,
    NativeRuntimeManifest,
    NativeRuntimeParameter,
    NativeRuntimeResponse,
    NativeRuntimeTool,
)

__all__ = [
    "ActionRegistry",
    "ActionRisk",
    "ActionSpec",
    "BackendExecutionResult",
    "BackendId",
    "BackendSequenceResult",
    "BackendVerification",
    "BrokerCapabilities",
    "CandidateIdentity",
    "ExecutionBackend",
    "ExecutionBroker",
    "ExecutionBrokerError",
    "ExecutionAuthority",
    "ExecutionSequenceResult",
    "ExecutionToolDescriptor",
    "ExecutionToolParameter",
    "NativeRuntimeBackend",
    "NativeRuntimeClient",
    "NativeRuntimeError",
    "NativeRuntimeManifest",
    "NativeRuntimeParameter",
    "NativeRuntimeResponse",
    "NativeRuntimeTool",
    "IdempotencyConflict",
    "OrderedActionSequence",
    "PreparedAction",
    "ResearchTarget",
    "SemanticTarget",
    "SourceSnapshot",
    "build_execution_broker",
    "builtin_action_registry",
]
