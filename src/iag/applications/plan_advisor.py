"""Optional narrow model pass for localized plan exceptions."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

from iag.core.application_plan import ApplicationPlanBook
from iag.infrastructure.llm.model_client import chat_completion_message
from iag.infrastructure.llm.model_pool import ModelPool
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.runtime_config import RuntimeConfig

FAST_ADVISOR_SYSTEM_PROMPT = """
你是 IAG 的快速局部异常参谋。输入只包含已经定位好的异常、相关计划片段和最少
事实。不得重新规划整个领域，不得修改未受影响节点，也不得要求完整世界状态。

只输出一个 JSON 对象：
{"decision":"continue|patch|escalate","reason":"...","patch":null,"handoff":null}

- continue：偏差很小，按当前观测值继续到它再次变化。
- patch：只改受影响的低层节点。patch 必须符合 edit_application_plan 参数；不得设置
  plan_status，不得改动无关或已完成节点。
- escalate：需要更高层或跨领域判断。handoff 应压缩说明异常、预期、实际、已排除
  的局部办法、仍成立部分、建议从哪一层重新规划，以及主模型需要的事实。

不要输出 Markdown、工具调用、隐藏推理或 JSON 之外的文字。信息不足或不确定时
直接 escalate。保持简短。
""".strip()


def _parse_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Fast adviser did not return a JSON object.")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("Fast adviser response must be a JSON object.")
    return value


class PlanExceptionAdvisor:
    """Route a localized exception through an optional lightweight profile."""

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        *,
        completion_fn: Callable[..., dict[str, Any]] = chat_completion_message,
    ) -> None:
        self.runtime_config = runtime_config
        self.completion_fn = completion_fn

    @staticmethod
    def _main_context(
        evaluation: dict[str, Any],
        *,
        adviser_handoff: Any = None,
        adviser_error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema": "iag.localized_plan_exception.v1",
            "plan_id": evaluation.get("plan_id"),
            "revision": evaluation.get("revision"),
            "decision": evaluation.get("decision"),
            "anomalies": copy.deepcopy(evaluation.get("anomalies", [])),
            "affected_nodes": copy.deepcopy(evaluation.get("affected_nodes", [])),
            "invalidated_node_ids": copy.deepcopy(
                evaluation.get("invalidated_node_ids", [])
            ),
            "preserved_node_ids": copy.deepcopy(
                evaluation.get("preserved_node_ids", [])
            ),
            "changed_facts": copy.deepcopy(evaluation.get("changed_facts", [])),
            "relevant_facts": copy.deepcopy(evaluation.get("relevant_facts", {})),
            "fast_adviser_handoff": copy.deepcopy(adviser_handoff),
            "fast_adviser_error": adviser_error,
        }

    @staticmethod
    def _scoped_patch(
        raw: Any,
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TypeError("patch decision requires a patch object.")
        affected = {str(item) for item in evaluation.get("invalidated_node_ids", [])}
        preserved = {str(item) for item in evaluation.get("preserved_node_ids", [])}
        affected_nodes = {
            str(item.get("node_id") or ""): item
            for item in evaluation.get("affected_nodes", [])
            if isinstance(item, dict) and item.get("node_id")
        }
        local_anchors = {
            str(reference)
            for node in affected_nodes.values()
            for reference in [node.get("parent_id"), *node.get("depends_on", [])]
            if reference is not None and str(reference) in preserved
        }
        patch = {
            "plan_id": str(evaluation.get("plan_id") or ""),
            "expected_revision": int(evaluation.get("revision") or 0),
            "observed_change": str(raw.get("observed_change") or ""),
            "reason": str(raw.get("reason") or ""),
            "invalidate_node_ids": list(raw.get("invalidate_node_ids", [])),
            "preserve_node_ids": list(raw.get("preserve_node_ids", [])),
            "remove_node_ids": list(raw.get("remove_node_ids", [])),
            "upsert_nodes": list(raw.get("upsert_nodes", [])),
            "resume_from_node_id": raw.get("resume_from_node_id"),
        }
        changed_existing = {
            str(item)
            for key in ("invalidate_node_ids", "remove_node_ids")
            for item in patch[key]
        }
        new_node_ids = {
            str(node.get("node_id") or "")
            for node in patch["upsert_nodes"]
            if isinstance(node, dict)
            and str(node.get("node_id") or "") not in affected | preserved
        }
        for node in patch["upsert_nodes"]:
            if not isinstance(node, dict):
                raise TypeError("Fast adviser upsert_nodes entries must be objects.")
            node_id = str(node.get("node_id") or "")
            if node_id in affected | preserved:
                changed_existing.add(node_id)
            if node_id not in new_node_ids:
                continue
            allowed_references = affected | new_node_ids | local_anchors
            parent_id = node.get("parent_id")
            if parent_id is None or str(parent_id) not in allowed_references:
                raise ValueError(
                    "Fast adviser new nodes must remain attached to the affected branch."
                )
            dependencies = {str(item) for item in node.get("depends_on", [])}
            if not dependencies.issubset(allowed_references):
                raise ValueError(
                    "Fast adviser new nodes depend on an unrelated plan branch."
                )
        if not changed_existing.issubset(affected):
            raise ValueError("Fast adviser attempted to modify an unaffected node.")
        resume = patch.get("resume_from_node_id")
        if resume is not None and str(resume) not in (
            affected | new_node_ids | local_anchors
        ):
            raise ValueError(
                "Fast adviser attempted to resume outside the local branch."
            )
        return patch

    def handle(
        self,
        evaluation: dict[str, Any],
        plan_book: ApplicationPlanBook,
    ) -> dict[str, Any]:
        base = self.runtime_config.snapshot()
        profile_id = str(base.settings.get("fast_advisor_profile_id") or "").strip()
        if not profile_id:
            return {
                "decision": "escalate",
                "configured": False,
                "main_context": self._main_context(evaluation),
            }
        try:
            runtime = self.runtime_config.snapshot_profile(profile_id)
            timeout = max(
                5,
                min(int(base.settings.get("fast_advisor_timeout_seconds", 60)), 60),
            )
            output_limit = max(
                256,
                min(
                    int(base.settings.get("fast_advisor_max_output_tokens", 1000)), 1600
                ),
            )
            active_endpoint_count = max(
                sum(endpoint.enabled for endpoint in runtime.model_pool.endpoints),
                1,
            )
            endpoint_timeout = max(5, timeout // active_endpoint_count)
            pool = runtime.model_pool.model_copy(
                update={
                    "endpoints": [
                        endpoint.model_copy(
                            update={
                                "timeout_seconds": min(
                                    endpoint.timeout_seconds,
                                    endpoint_timeout,
                                ),
                                "max_output_tokens": min(
                                    endpoint.max_output_tokens,
                                    output_limit,
                                ),
                                "sdk_max_retries": 0,
                            },
                            deep=True,
                        )
                        for endpoint in runtime.model_pool.endpoints
                    ]
                },
                deep=True,
            )
            pool = ModelPool.model_validate(pool.model_dump(mode="python"))
            model_runtime = ModelPoolRuntime(pool)
            request_options = dict(runtime.request_options)
            overrides = dict(request_options.get("request_body_overrides", {}))
            overrides["max_tokens"] = output_limit
            request_options["request_body_overrides"] = overrides
            response = model_runtime.execute(
                lambda endpoint: self.completion_fn(
                    endpoint,
                    [
                        {"role": "system", "content": FAST_ADVISOR_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                self._main_context(evaluation),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                    request_options=request_options,
                    tools=None,
                )
            )
            answer = _parse_json_object(str(response.get("content") or ""))
            decision = str(answer.get("decision") or "").strip().lower()
            reason = str(answer.get("reason") or "").strip()
            if decision == "continue":
                plan_book.accept_localized_anomaly(
                    evaluation,
                    reason=reason or "快速参谋确认该局部偏差不影响原计划。",
                )
                return {
                    "decision": "continue",
                    "configured": True,
                    "profile_id": profile_id,
                    "reason": reason,
                }
            if decision == "patch":
                patch = self._scoped_patch(answer.get("patch"), evaluation)
                result = plan_book.edit(patch)
                return {
                    "decision": "patched",
                    "configured": True,
                    "profile_id": profile_id,
                    "reason": reason,
                    "plan": result.get("plan"),
                }
            if decision != "escalate":
                raise ValueError(f"Unsupported fast adviser decision: {decision!r}")
            handoff = answer.get("handoff")
            return {
                "decision": "escalate",
                "configured": True,
                "profile_id": profile_id,
                "reason": reason,
                "main_context": self._main_context(
                    evaluation,
                    adviser_handoff=handoff,
                ),
            }
        except Exception as error:  # noqa: BLE001 - main model is the fallback
            detail = f"{type(error).__name__}: {error}"
            return {
                "decision": "escalate",
                "configured": True,
                "profile_id": profile_id,
                "error": detail,
                "main_context": self._main_context(
                    evaluation,
                    adviser_error=detail,
                ),
            }
