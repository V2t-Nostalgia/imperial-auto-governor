"""Private bridge from reviewed packet targets to capture-backed runtime input.

Most generated native actions expose the already validated scalar fields from
their paired packet target. Two commands contain nested variable-length data;
for those only, this module asks the existing packet builder to serialize the
reviewed target and passes the command-object body to the native factory.

This is diagnostics/backend code. Applications and models must use semantic
targets through the Execution Broker and must never provide record hex.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from iag.stellaris.execution.protocol_compatibility import COMMAND_SPEC_BY_ACTION
from iag.stellaris.execution.session_proxy import (
    _build_request_record,
    parse_arm_document,
)

_RAW_BODY_ACTIONS: Final = frozenset(
    {"configure_ship_automation", "create_ship_design"}
)
_SESSION_ID: Final = "native-capture-materializer"


def adapt_capture_target(
    *,
    action_type: str,
    target: Mapping[str, Any],
    country_id: int,
) -> dict[str, Any]:
    """Return the private runtime target for one capture-backed action."""

    if action_type not in COMMAND_SPEC_BY_ACTION:
        raise ValueError(f"Unknown captured action: {action_type!r}.")
    if action_type not in _RAW_BODY_ACTIONS:
        return dict(target)
    request = parse_arm_document(
        {
            "request_id": f"materialize-{action_type}",
            "session_id": _SESSION_ID,
            "action": action_type,
            "source_actor": country_id,
            "host_actor": country_id,
            "request_origin": 0,
            "target": dict(target),
        },
        _SESSION_ID,
    )
    record = _build_request_record(request, command_serial=1)
    if len(record) < 12 or record[2:6] != bytes.fromhex("04000000"):
        raise ValueError("The packet builder returned an invalid command envelope.")
    body = record[6:]
    expected_family = COMMAND_SPEC_BY_ACTION[action_type].wire_family_hex.lower()
    if body[:6].hex() != expected_family:
        raise ValueError(
            f"The built command family does not match {action_type!r}."
        )
    return {"verified_record_hex": body.hex()}


def uses_serialized_body_adapter(action_type: str) -> bool:
    """Report whether an action needs the isolated nested-record adapter."""

    return action_type in _RAW_BODY_ACTIONS
